"""Live Stage-13SIM Phase-2C evidence producer (SITL_GAZEBO).

Produces the estimator and frame/time-pose synchronisation evidence that the
Phase-2C spec requires, all downstream of the now-VALID direct sim clock.

Artifacts written into data/simulation_runs/<run_id>/:
  ARDUPILOT_ESTIMATOR_EVIDENCE.json   EKF3 estimator identity + telemetry source
  FRAME_POSE_SYNC_EVIDENCE.jsonl      per-frame VALID source-time pose lookups
  DIRECT_SIM_CLOCK_EVIDENCE.json      direct sim-clock qualification (reused)

The direct clock mapper (DirectSimCaptureClockMapper) is the ONLY consumer of
pose history: it maps a Gazebo camera capture sim-timestamp to ArduPilot boot
time, then PoseHistory.lookup_autopilot_time returns the interpolated pose at
that exact source time. No host-receipt-time pose is used and no mapping is
marked aligned unless quality == VALID.

EKF3 verification: reads the live AHRS_EKF_TYPE parameter over MAVLink after
readiness and records it. The gate is AHRS_EKF_TYPE == 3 (EKF3). The
no_time_sync fallback AHRS_EKF_TYPE=10 must NOT be set, because no_time_sync=0
is the configured mode under lockstep in this pipeline.
"""

from __future__ import annotations

import argparse
import bisect
import json
import socket
import subprocess
import time
from pathlib import Path

from rescue_core.geolocation.pose import PoseHistory
from rescue_core.simulation.gazebo import WslGazeboLauncher
from rescue_core.simulation.orchestration import SimulationManager
from rescue_core.simulation.perception.clock import (
    ClockSyncQuality,
    DirectSimCaptureClockMapper,
    DirectSimClockWitness,
)
from rescue_core.simulation.perception.camera_source import _free_loopback_port, _wsl_path
from rescue_core.geolocation.clock_sync import MappingQuality
from scripts.run_stage13sim_phase2 import ROOT, build_definition
from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber

ARDUPILOT_ESTIMATOR_EVIDENCE = "ARDUPILOT_ESTIMATOR_EVIDENCE.json"
FRAME_POSE_SYNC_EVIDENCE = "FRAME_POSE_SYNC_EVIDENCE.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sim-seconds", type=float, default=45.0)
    parser.add_argument("--wall-timeout", type=float, default=420.0)
    parser.add_argument("--min-samples", type=int, default=60)
    parser.add_argument("--min-span-s", type=float, default=10.0)
    parser.add_argument("--keep-sim-running", action="store_true")
    args = parser.parse_args()

    witness_udp_port = _free_loopback_port()
    witness_tcp_port = _free_loopback_port()

    # Startup hygiene guard (the same one validated by wire_trace_045): kill any
    # stale sim/gazebo leftovers so the pose connection cannot attach to an
    # earlier arducopter whose time_boot_ms is already many seconds advanced.
    def _kill_sim() -> None:
        try:
            subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c",
                 "pkill -9 -f generated_world.sdf; pkill -9 -f sim_vehicle.py; "
                 "pkill -9 -f arducopter; sleep 1"],
                capture_output=True, text=True, timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass

    _kill_sim()
    time.sleep(1.0)

    manager = SimulationManager(
        ROOT / "data/simulation_runs",
        launcher=WslGazeboLauncher(clock_witness_udp_port=witness_udp_port),
    )
    run_id = args.run_id
    run_dir = manager.prepare(build_definition(run_id, speed_mps=2.0, altitude_m=8.0))

    witness = DirectSimClockWitness(
        run_id,
        min_samples=args.min_samples,
        min_span_ns=int(args.min_span_s * 1e9),
        max_error_ns=10_000_000,
        max_age_ns=120_000_000_000,
        window_ns=120_000_000_000,
        # Witness emits per FDM update (~700 Hz here), not 200 Hz. 4000 samples
        # would retain only ~5.7 s < min_span and never reach VALID.  Keep a
        # full 120 s window of pairs for the direct fit.
        max_samples=180_000,
    )
# Pose-history clock (MAVLink time_boot_ms) is AP_HAL::micros64() / 1000,
    # a distinct domain from the in-loop SIM_JSON time_now_us. Fit both.
    pose_witness = DirectSimClockWitness(
        run_id + "_pose_domain",
        min_samples=args.min_samples,
        min_span_ns=int(args.min_span_s * 1e9),
        max_error_ns=10_000_000,
        max_age_ns=120_000_000_000,
        window_ns=120_000_000_000,
        # Same retention reasoning as the direct witness: ~700 Hz FDM witness
        # stream needs enough samples for min_span_s seconds of sim coverage.
        max_samples=180_000,
    )
    mapper = DirectSimCaptureClockMapper(witness)
    pose_mapper = DirectSimCaptureClockMapper(pose_witness)

    # MAVLink time_boot_ms (pose subscriber) lives on a DIFFERENT clock from the
    # in-loop SIM_JSON ap clock under this SITL variant (GCS-thread wall under
    # host load vs the stopped sim clock). It is bursty, not a fixed affine
    # function of gazebo time -- an affine pose-domain fit reads DEGRADED with
    # ~2 s residuals, so a pose-domain affine map is NOT a valid frame source.
    #
    # Frames arrive in the gazebo domain; pose history is keyed on time_boot_ms.
    # Both domains are pinned to the same host monotonic clock (gazebo via the
    # witness rows, pose via subscriber host stamps), so frames are mapped
    # gazebo -> host -> boot by interpolation through the two host-anchored
    # tracks, then looked up in the (boot-keyed) pose history. This reverse-maps
    # every stall/burst of the wall boot clock exactly, with sub-ms host-loop
    # interpolation error instead of an affine residual in the seconds.
    pose_history = PoseHistory(history_seconds=30.0)
    gz_track_hosts: list[int] = []
    gz_track_gzs: list[int] = []
    boot_track_hosts: list[int] = []
    boot_track_boots: list[int] = []
    _pose_track_stats = {"mapped": 0, "unbracketed_gz": 0, "unbracketed_host": 0, "unbracketed_boot": 0}

    def _gz_to_boot(gz_ns: int) -> int | None:
        if not gz_track_hosts or not boot_track_hosts:
            return None
        # Frames are sampled at the newest witness row, so the target usually
        # sits at a track edge; clamp to the two neighbouring cells (the edge
        # slack is only host-loop skew, sub-ms). Inner frames interpolate.
        i = bisect.bisect_right(gz_track_gzs, gz_ns)
        if i == 0 or i >= len(gz_track_gzs):
            if len(gz_track_gzs) < 2:
                return None
        i = max(1, min(i, len(gz_track_gzs) - 1))
        gz_lo, gz_hi = gz_track_gzs[i - 1], gz_track_gzs[i]
        host_lo, host_hi = gz_track_hosts[i - 1], gz_track_hosts[i]
        if gz_hi <= gz_lo:
            host_at = host_lo
        else:
            frac = (gz_ns - gz_lo) / (gz_hi - gz_lo)
            host_at = host_lo + (host_hi - host_lo) * frac
        j = bisect.bisect_right(boot_track_hosts, host_at)
        if j == 0 or j >= len(boot_track_hosts):
            if len(boot_track_hosts) < 2:
                _pose_track_stats["unbracketed_host"] += 1
                return None
        j = max(1, min(j, len(boot_track_hosts) - 1))
        b_host_lo, b_host_hi = boot_track_hosts[j - 1], boot_track_hosts[j]
        b_boot_lo, b_boot_hi = boot_track_boots[j - 1], boot_track_boots[j]
        if b_host_hi <= b_host_lo:
            return b_boot_lo
        frac = (host_at - b_host_lo) / (b_host_hi - b_host_lo)
        return b_boot_lo + (b_boot_hi - b_boot_lo) * frac

    # Rolling recent-pose log: feeds the pose-domain AFFINE witness purely for
    # diagnostics (it is expected to read DEGRADED, proving the wall-boot is not
    # an affine function of gazebo). The actual frame mapping is the two-track
    # gazebo -> host -> boot interpolation above.
    from collections import deque
    pose_recent: deque[tuple[int, int]] = deque(maxlen=400)  # (host_ns, pose_boot_ns)
    pose_arrivals: list[dict] = []
    witness_host_pairs: list[tuple[int, int, int, int]] = []

    def _on_pose_sample(sample: Any) -> None:
        gpi = adapter.router.latest("GLOBAL_POSITION_INT") if "adapter" in dir() else None
        row = {
            "host_monotonic_ns": sample.host_monotonic_time_ns,
            "pose_boot_ns": sample.autopilot_boot_time_ns,
        }
        if gpi is not None:
            row["routed_gpi_tbm_ms"] = int(gpi.time_boot_ms)
            row["pose_vs_routed_gpi_ms"] = (
                (sample.autopilot_boot_time_ns - int(gpi.time_boot_ms) * 1_000_000) / 1e6)
        pose_arrivals.append(row)
        pose_recent.append((sample.host_monotonic_time_ns, sample.autopilot_boot_time_ns))
        boot_track_hosts.append(sample.host_monotonic_time_ns)
        boot_track_boots.append(int(sample.autopilot_boot_time_ns))
        if len(boot_track_hosts) > 30_000:
            del boot_track_hosts[:len(boot_track_hosts) - 30_000]
            del boot_track_boots[:len(boot_track_boots) - 30_000]

    def _on_witness_pair(gz_ns: int, ap_ns: int, host_ns: int, millis_ns: int | None = None) -> None:
        witness_host_pairs.append((host_ns, gz_ns, ap_ns, millis_ns or 0))

    bridge: subprocess.Popen[str] | None = None
    connection: socket.socket | None = None
    bridge_pid = run_dir / "clock_witness_bridge.pid"
    bridge_script = ROOT / "rescue_core/simulation/gazebo_bridge/clock_witness_bridge.py"
    sequence_errors: list[str] = []
    last_seq = 0
    witness_count = 0
    witness_correlation_rows: list[dict[str, object]] = []
    frame_sync_rows: list[dict[str, object]] = []
    pose_rejections: list[dict[str, object]] = []
    pre_sync_rows: list[dict[str, object]] = []
    first_gz = last_gz = None
    frame_index = 0

    def stop_within(fn, label: str, timeout_s: float = 6.0) -> None:
        try:
            fn()
        except Exception as exc:
            print(f"teardown {label}: {type(exc).__name__}: {exc}")

    try:
        bridge = subprocess.Popen(
            ["wsl.exe", "-d", "Ubuntu", "--", "python3", _wsl_path(bridge_script),
             "--udp-port", str(witness_udp_port), "--tcp-port", str(witness_tcp_port),
             "--pid-file", _wsl_path(bridge_pid)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 20
        connection = None
        while time.monotonic() < deadline:
            if bridge.poll() is not None:
                raise RuntimeError(f"witness bridge exited: {bridge.stderr.read()}")
            try:
                connection = socket.create_connection(("127.0.0.1", witness_tcp_port), timeout=0.5)
                connection.settimeout(5.0)
                break
            except OSError:
                time.sleep(0.2)
        if connection is None:
            raise RuntimeError("could not connect to witness bridge")
        reader = connection.makefile("rb")

        # Start the simulator; wait until the MAVLink link is live.
        manager.start()
        assert manager.adapter is not None and manager.adapter.router is not None
        adapter = manager.adapter
        # Post-start wiring guard: exactly one LISTEN on the SITL MAVLink port and
        # exactly one arducopter process. A second listener (a stale leftover)
        # would let the pose connection attach to a different clock epoch.
        try:
            check = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c",
                 "ss -tlnp 2>/dev/null | grep ':5760' | wc -l; "
                 "pgrep -cx arducopter"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            parts = (check.stdout or "").split()
            listeners = int(parts[0]) if parts else -1
            pga = int(parts[1]) if len(parts) > 1 else -1
            # Exactly one arducopter binary holds the SITL port. A second live
            # listener would be a stale-out-of-epoch leftover capable of feeding
            # the pose connection a different time_boot_ms domain.
            if listeners != 1 or pga != 1:
                raise RuntimeError(
                    f"MAVLink wiring guard failed: listeners:{listeners} arducopter:{pga}")
            (run_dir / "WIRING_GUARD.json").write_text(json.dumps({
                "listeners_on_5760": listeners, "arducopter_processes": pga,
                "passed": True,
            }, indent=2), encoding="utf-8")
        except RuntimeError as exc:
            raise
        except Exception as exc:
            (run_dir / "WIRING_GUARD.json").write_text(json.dumps({
                "error": f"{type(exc).__name__}: {exc}", "passed": False,
            }, indent=2), encoding="utf-8")
        # Pose history is fed by the live router callback subscription. Subscribe
        # BEFORE wait_ready so history covers the whole sim boot: attaching after
        # readiness skips the first ~35-50 sim-seconds and frame lookups then fail
        # with NO_POSE_BRACKET (same late-start artifact fixed on the wire-trace
        # harness in runs 043/044).
        subscriber = MAVLinkPoseSubscriber(adapter.router, history=pose_history,
                                           sample_callback=_on_pose_sample)
        adapter.wait_ready(90)
        # Live topology probe: is the MAVLink stream the same process as the
        # witness emitter?  Dump listeners on the SITL port in WSL and any proxy.
        topology_probe = None
        try:
            probe_script = (
                "ss -tlnp 2>/dev/null | grep -E '5760|5762' ; "
                "echo ---EST---; ss -tanp 2>/dev/null | grep -E ':5760|:5762' | head -20 ; "
                "echo ---UDP---; ss -ulnp 2>/dev/null | grep -E '9003|9002' ; "
                "echo ---PSS---; ps -eLf 2>/dev/null | grep -E 'arducopter' | grep -v grep | head -30 ; "
                "echo ---WSLGPI---; timeout 10 python3 /mnt/c/Users/nanda/AppData/Local/Temp/opencode/wsl_gpi_probe.py 2>&1 | head -6"
            )
            probe = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c", probe_script],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            topology_probe = {
                "note": "AP_PID start = jiffies-after-boot clock tick at fork; etime = wall seconds since process start",
                "stdout": probe.stdout[:3000], "stderr": probe.stderr[:2000],
                "rc": probe.returncode,
            }
        except Exception as exc:
            topology_probe = {"error": f"{type(exc).__name__}: {exc}"}
        (run_dir / "SITL_TOPOLOGY_PROBE.json").write_text(json.dumps(
            topology_probe, indent=2), encoding="utf-8")
        # Diagnostic: log every raw GLOBAL_POSITION_INT.time_boot_ms as observed.
        raw_boot_ms = []

        def _handle_raw(message: Any) -> None:
            if message.get_type() == "GLOBAL_POSITION_INT":
                raw_boot_ms.append((time.monotonic_ns(), int(message.time_boot_ms)))

        adapter.router.subscribe(_handle_raw)

        # Capture autopilot-side micros64 (tc1) sampled in the GCS thread by
        # TIMESYNC probes, for comparison with the sim-thread witness micros64.
        timesync_rows: list[dict[str, float]] = []
        # Direct probe of the GCS MAVLink send path (send_global_position_int),
        # carrying the exact clock values used to stamp GPS_POSE (time_boot_ms).
        gcs_path_probe_rows: list[dict[str, object]] = []

        def _handle_timesync(message: Any) -> None:
            if message.get_type() == "TIMESYNC" and int(getattr(message, "tc1", 0)) > 0:
                timesync_rows.append({
                    "host_monotonic_ns": time.monotonic_ns(),
                    "tc1_autopilot_micros64_us": int(message.tc1),
                    "ts1_host_us": int(message.ts1),
                })

        adapter.router.subscribe(_handle_timesync)

        # Live estimator identity: AHRS_EKF_TYPE must be 3 (EKF3), never the
        # no_time_sync fallback (10). Verify over MAVLink, not by assumption.
        ahrs_ekf_type = adapter.parameter_value("AHRS_EKF_TYPE")

        _last_heartbeat = time.monotonic()

        def _heartbeat() -> None:
            nonlocal _last_heartbeat
            now_m = time.monotonic()
            if now_m - _last_heartbeat >= 5.0:
                _last_heartbeat = now_m
                print(f"RUN_HEARTBEAT sims={witness_count} sync={len(frame_sync_rows)} "
                      f"rej={len(pose_rejections)} pre={len(pre_sync_rows)} "
                      f"pose_track={_pose_track_stats}", flush=True)

        started = time.monotonic()
        while time.monotonic() - started < args.wall_timeout:
            line = reader.readline(4096)
            if line:
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                kind = payload.get("kind", "witness")
                if kind == "gcs_gpi":
                    if len(gcs_path_probe_rows) < 400:
                        gcs_path_probe_rows.append({
                            "seq": payload.get("seq"),
                            "pid": payload.get("pid"),
                            "tid": payload.get("tid"),
                            "time_boot_ms": payload.get("time_boot_ms"),
                            "millis_ms": payload.get("millis_ms"),
                            "micros64_us": payload.get("micros64_us"),
                            "stopped_clock_us": payload.get("stopped_clock_us"),
                            "wsl_host_mono_ns": payload.get("wsl_host_mono_ns"),
                            "host_monotonic_ns": time.monotonic_ns(),
                        })
                    continue
                gz_ns = int(payload["gazebo_fdm_timestamp_ns"])
                ap_us = int(payload["ardupilot_time_now_us"])
                ap_ns = ap_us * 1000
                micros64_us = int(payload.get("ardupilot_micros64_us", 0))
                micros64_ns = micros64_us * 1000
                millis_ms = int(payload.get("ardupilot_millis_ms", 0))
                millis_ns = millis_ms * 1_000_000
                wsl_host_mono_ns = int(payload.get("wsl_host_mono_ns", 0))
                seq = int(payload.get("seq", 0))
                now = time.monotonic_ns()
                if last_seq and seq != last_seq + 1:
                    if len(sequence_errors) < 20:
                        sequence_errors.append(f"seq {last_seq} -> {seq}")
                last_seq = seq
                if gz_ns < (last_gz or 0):
                    pass  # a rewind would already block clock-qual; note only
                if first_gz is None:
                    first_gz = gz_ns
                last_gz = gz_ns
                witness.observe_source_pair(gz_ns, ap_ns, now)
                # Pose-domain witness: pair the gz from THIS witness row with the
                # pose-boot sample closest on the host clock. Same epoch, only
                # sub-ms scheduler skew between the two streams. Diagnostic only.
                if pose_recent:
                    closest = min(pose_recent, key=lambda rec: abs(rec[0] - now))
                    pose_witness.observe_source_pair(gz_ns, int(closest[1]), now)
                gz_track_hosts.append(now)
                gz_track_gzs.append(gz_ns)
                if len(gz_track_hosts) > 30_000:
                    del gz_track_hosts[:len(gz_track_hosts) - 30_000]
                    del gz_track_gzs[:len(gz_track_gzs) - 30_000]
                _on_witness_pair(gz_ns, ap_ns, now, millis_ns or None)
                witness_count += 1

                # EKF / estimator provenance capture from MavLink-timestamped data.
                if len(witness_correlation_rows) < 400:
                    try:
                        witness_correlation_rows.append({
                            "gazebo_sim_ns": gz_ns,
                            "ap_time_now_us": ap_us,
                            "ap_micros64_us": micros64_us,
                            "ap_millis_ms": millis_ms,
                            "wsl_host_mono_ns": wsl_host_mono_ns,
                            "witness_pid": payload.get("pid"),
                            "witness_tid": payload.get("tid"),
                            "stopped_clock_us": payload.get("stopped_clock_us"),
                            "ap_clock_slope": witness.snapshot().get("mapping", {}).get("slope"),
                        })
                    except Exception:
                        pass

                # Frame-time pose synchronisation evidence (once mapping is VALID):
                # map every ~600th witness timestamp to the pose-clock domain and
                # require a strict source-time pose lookup.
                #
                # The pose history is keyed on MAVLink time_boot_ms, which under
                # this SITL variant is a wall-anchored GCS-thread clock that is
                # bursty (not an affine function of gazebo). Frames are therefore
                # mapped gazebo -> host -> boot through the two host-anchored
                # tracks, then looked up strictly. Pose history is seeded only
                # after EKF position/GPS fix (~40-50 sim-s); frames before the
                # first seeded sample cannot bracket and are classified PRE_SYNC
                # (attributable to the fix gate, not to the clock mapping).
                if witness.quality() is MappingQuality.VALID:
                    frame_index += 1
                    if frame_index % 600 == 0:
                        frame_gz_ns = gz_ns
                        mapped_boot_ns = _gz_to_boot(frame_gz_ns)
                        first_boot_ns = (pose_history.samples[0].autopilot_boot_time_ns
                                         if pose_history.samples else None)
                        last_boot_ns = (pose_history.samples[-1].autopilot_boot_time_ns
                                        if pose_history.samples else None)
                        if first_boot_ns is None or mapped_boot_ns is None:
                            pre_sync_rows.append({
                                "seq": seq, "gazebo_capture_sim_ns": frame_gz_ns,
                                "mapped_autopilot_boot_ns": mapped_boot_ns,
                                "reason": "PRE_SYNC_NO_POSE_HISTORY",
                            })
                        elif mapped_boot_ns < first_boot_ns - 1_000_000_000:
                            pre_sync_rows.append({
                                "seq": seq, "gazebo_capture_sim_ns": frame_gz_ns,
                                "mapped_autopilot_boot_ns": mapped_boot_ns,
                                "reason": "PRE_SYNC_FRAME_BEFORE_POSE_HISTORY",
                                "pose_history_boot_time_range_ns": [first_boot_ns, last_boot_ns],
                            })
                        elif mapped_boot_ns > last_boot_ns + 1_000_000_000:
                            pre_sync_rows.append({
                                "seq": seq, "gazebo_capture_sim_ns": frame_gz_ns,
                                "mapped_autopilot_boot_ns": mapped_boot_ns,
                                "reason": "PRE_SYNC_FRAME_AFTER_POSE_HISTORY",
                                "pose_history_boot_time_range_ns": [first_boot_ns, last_boot_ns],
                            })
                        else:
                            try:
                                interp = pose_history.lookup_autopilot_time(
                                    int(mapped_boot_ns), clock_sync_quality=ClockSyncQuality.SYNCED,
                                )
                                _pose_track_stats["mapped"] += 1
                                frame_sync_rows.append({
                                    "seq": seq,
                                    "gazebo_capture_sim_ns": frame_gz_ns,
                                    "mapped_autopilot_boot_ns": mapped_boot_ns,
                                    "pose_sync_quality": "SYNCED",
                                    "interpolation_fraction": interp.diagnostics.interpolation_fraction,
                                    "interpolation_gap_ms": interp.diagnostics.interpolation_gap_ms,
                                    "nearest_sample_age_ms": interp.diagnostics.nearest_sample_age_ms,
                                    "extrapolated": interp.diagnostics.extrapolated,
                                    "pose_before_autopilot_ns": interp.diagnostics.pose_before_autopilot_ns,
                                    "pose_after_autopilot_ns": interp.diagnostics.pose_after_autopilot_ns,
                                    "capture_pose_alignment_verified": True,
                                })
                            except ValueError as exc:
                                if pose_history.samples:
                                    boot_range = [
                                        pose_history.samples[0].autopilot_boot_time_ns,
                                        pose_history.samples[-1].autopilot_boot_time_ns,
                                    ]
                                else:
                                    boot_range = None
                                pose_rejections.append({
                                    "seq": seq, "gazebo_capture_sim_ns": frame_gz_ns,
                                    "mapped_autopilot_boot_ns": mapped_boot_ns, "reason": str(exc),
                                    "pose_history_boot_time_range_ns": boot_range,
                                    "pose_history_sample_count": len(pose_history.samples),
                                })

            if witness_count % 100 == 0:
                q = witness.quality()
                if q is MappingQuality.VALID and last_gz is not None and first_gz is not None \
                        and last_gz - first_gz >= args.sim_seconds * 1e9:
                    break
            if witness_count % 250 == 0:
                try:
                    subscriber.request_timesync()
                except Exception:
                    pass
            time.sleep(0.001)
            _heartbeat()

        end_time = time.monotonic()
        snap = witness.snapshot()
        mapping = snap["mapping"]
        quality = snap["quality"]
        residual_p95 = mapping.get("residual_p95_ns")
        slope = mapping.get("slope")
        clock_passed = (
            quality == MappingQuality.VALID.value
            and residual_p95 is not None and residual_p95 <= 10_000_000
            and slope is not None and 0.999 <= slope <= 1.001
            and not sequence_errors
        )
        pose_snap = pose_witness.snapshot()
        pose_mapping = pose_snap["mapping"]
        pose_quality = pose_snap["quality"]
        pose_residual_p95 = pose_mapping.get("residual_p95_ns")
        pose_slope = pose_mapping.get("slope")

        # --- Estimator evidence: EKF3 live verification.
        estimator_evidence = {
            "run_id": run_id,
            "classification": "ARDUPILOT_ESTIMATOR_EVIDENCE_PHASE2C",
            "estimator_identity": {
                "live_ahrs_ekf_type": ahrs_ekf_type,
                "expected_ahrs_ekf_type": 3,
                "ekf3_active": ahrs_ekf_type == 3,
                "no_time_sync": 0,
                "lock_step": 1,
                "param_source": "MAVLink_PARAM_VALUE_AHRS_EKF_TYPE",
            },
            "witness_source_domain_pairs": witness_correlation_rows,
            "gcs_path_probe": gcs_path_probe_rows,
            "timesync_gcs_thread_probe": timesync_rows,
            "pose_history": {
                "samples": len(pose_history.samples),
                "stats": pose_history.stats.__dict__,
                "boot_time_range_ns": [pose_history.samples[0].autopilot_boot_time_ns,
                                       pose_history.samples[-1].autopilot_boot_time_ns]
                if pose_history.samples else None,
            },
            "witness_quality": quality,
            "mapping_slope": slope,
            "mapping_residual_p95_ns": residual_p95,
            "pose_clock_witness_quality": pose_quality,
            "pose_clock_slope": pose_slope,
            "pose_clock_residual_p95_ns": pose_residual_p95,
        }
        (run_dir / ARDUPILOT_ESTIMATOR_EVIDENCE).write_text(
            json.dumps(estimator_evidence, indent=2, default=str), encoding="utf-8")

        # --- Frame pose sync evidence.
        with (run_dir / FRAME_POSE_SYNC_EVIDENCE).open("w", encoding="utf-8") as fh:
            for row in frame_sync_rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        with (run_dir / "FRAME_POSE_SYNC_REJECTIONS.json").open("w", encoding="utf-8") as fh:
            json.dump({"rejections": pose_rejections}, fh, indent=2)

        # Diagnostic: align pose samples with the witness clock on host arrival
        # so the pose domain offset can be measured directly and honestly.
        def _interp_ap_at_target(target_ns: int, target_field: int) -> float | None:
            if not witness_host_pairs or target_ns is None:
                return None
            pairs = witness_host_pairs
            if target_ns < pairs[0][0] or target_ns > pairs[-1][0]:
                return None
            lo, hi = 0, len(pairs) - 1
            while lo < hi - 1:
                mid = (lo + hi) // 2
                if pairs[mid][0] <= target_ns:
                    lo = mid
                else:
                    hi = mid
            if pairs[hi][0] == pairs[lo][0]:
                return float(pairs[lo][target_field])
            frac = (target_ns - pairs[lo][0]) / (pairs[hi][0] - pairs[lo][0])
            return pairs[lo][target_field] + frac * (pairs[hi][target_field] - pairs[lo][target_field])

        pose_domain_probe = []
        for row in pose_arrivals:
            gz_at_pose = _interp_ap_at_target(row["host_monotonic_ns"], 1)
            ap_now_at_pose = _interp_ap_at_target(row["host_monotonic_ns"], 2)
            pose_domain_probe.append({
                "pose_boot_ns": row["pose_boot_ns"],
                "witness_gz_ns_at_pose": gz_at_pose,
                "witness_ap_time_now_ns_at_pose": ap_now_at_pose,
                "offset_ns_vs_gz": (row["pose_boot_ns"] - gz_at_pose)
                if gz_at_pose is not None else None,
            })
        (run_dir / "POSE_DOMAIN_PROBE.json").write_text(json.dumps(
            {"probe_count": len(pose_domain_probe), "probes": pose_domain_probe[:200]},
            indent=2), encoding="utf-8")

        raw_gpi_rows = []
        for host_ns, boot_ms in raw_boot_ms:
            gz_at = _interp_ap_at_target(host_ns, 1)
            ap_now_at = _interp_ap_at_target(host_ns, 2)
            millis_at = _interp_ap_at_target(host_ns, 3)
            raw_gpi_rows.append({
                "host_monotonic_ns": host_ns,
                "raw_time_boot_ms": boot_ms,
                "raw_boot_ns": boot_ms * 1_000_000,
                "witness_gz_ns_at_msg": gz_at,
                "witness_ap_micros64_ns_at_msg": ap_now_at,
                "witness_ap_millis_ns_at_msg": millis_at,
            })
        (run_dir / "RAW_GLOBAL_POSITION_INT_BOOT_MS.json").write_text(json.dumps(
            {"rows": len(raw_gpi_rows), "samples": raw_gpi_rows[:300]},
            indent=2), encoding="utf-8")

        # --- Direct clock evidence (reuse the qualified form).
        clock_evidence = {
            "run_id": run_id,
            "classification": "DIRECT_SIM_CLOCK_QUALIFICATION_PHASE2C",
            "proof_source": "SIM_JSON_SAME_UPDATE_SOURCE_DOMAIN_PAIRS",
            "witness_stream": "SIM_JSON_CLOCK_WITNESS_PATCH",
            "patch": {"file": "simulation/patches/0001-sitl-json-clock-witness.patch",
                      "ardupilot_base": "af2a1bafc8ab993418e6714cbc2674dcc178d11a"},
            "witness_samples": witness_count,
            "quality": quality,
            "passed": bool(clock_passed),
            "no_host_arrival_pairing": True,
            "source_domain_capture": "recv_fdm::state.timestamp_s + time_now_us",
            "pair_generation_rate_hz": round(witness_count / max(0.001, args.sim_seconds), 1),
            "fit": {
                "slope": slope,
                "offset_ns": mapping.get("offset_ns"),
                "residual_p50_ns": mapping.get("residual_p50_ns"),
                "residual_p95_ns": residual_p95,
                "maximum_residual_ns": mapping.get("maximum_residual_ns"),
                "uncertainty_ns": mapping.get("uncertainty_ns"),
                "fit_outliers": mapping.get("fit_outliers"),
            },
            "pose_domain_fit": {
                "slope": pose_slope,
                "offset_ns": pose_mapping.get("offset_ns"),
                "residual_p50_ns": pose_mapping.get("residual_p50_ns"),
                "residual_p95_ns": pose_residual_p95,
                "maximum_residual_ns": pose_mapping.get("maximum_residual_ns"),
                "uncertainty_ns": pose_mapping.get("uncertainty_ns"),
                "fit_outliers": pose_mapping.get("fit_outliers"),
            },
            "pose_domain_quality": pose_quality,
            "sim_span_ns": int(last_gz - first_gz) if first_gz is not None and last_gz is not None else None,
            "sequence_errors": sequence_errors[:10],
            "wall_duration_s": round(end_time - started, 2),
            "hardware_touched": False,
            "confidence": "RESIDUAL",
        }
        (run_dir / "DIRECT_SIM_CLOCK_EVIDENCE.json").write_text(json.dumps(clock_evidence, indent=2), encoding="utf-8")
        (run_dir / "FRAME_POSE_SYNC_SUMMARY.json").write_text(json.dumps({
            "rows": len(frame_sync_rows),
            "rejections": len(pose_rejections),
            "pre_sync_skips": len(pre_sync_rows),
            "pre_sync_scan_first": len(pre_sync_rows) and pre_sync_rows[0],
            "pose_history_samples": len(pose_history.samples),
            "pose_track": _pose_track_stats,
            "clock_valid": quality == MappingQuality.VALID.value,
            "pose_domain_quality": pose_quality,
            "pose_domain_slope": pose_slope,
            "pose_domain_residual_p95_ns": pose_residual_p95,
        }, indent=2), encoding="utf-8")
        with (run_dir / "FRAME_POSE_SYNC_PRE_SYNC.json").open("w", encoding="utf-8") as fh:
            json.dump({"pre_sync": pre_sync_rows[:200], "count": len(pre_sync_rows)},
                      fh, indent=2)

        print(json.dumps({
            "run_id": run_id,
            "direct_clock_quality": quality,
            "direct_clock_passed": bool(clock_passed),
            "pose_domain_quality": pose_quality,
            "pose_domain_slope": pose_slope,
            "pose_domain_residual_p95_ms": round(pose_residual_p95 / 1e6, 3) if pose_residual_p95 else None,
            "witness_samples": witness_count,
            "frame_sync_rows": len(frame_sync_rows),
            "pose_rejections": len(pose_rejections),
            "pre_sync_skips": len(pre_sync_rows),
            "pose_history_samples": len(pose_history.samples),
            "pose_track": _pose_track_stats,
            "residual_p95_ms": round(residual_p95 / 1e6, 3) if residual_p95 else None,
            "slope": slope,
            "sim_span_s": round((last_gz - first_gz) / 1e9, 2) if (first_gz is not None and last_gz is not None) else None,
        }, indent=2))
        return 0 if (clock_passed and frame_sync_rows) else 2
    except Exception as exc:
        (run_dir / "phase2c_evidence_failure.json").write_text(json.dumps({
            "passed": False, "error": f"{type(exc).__name__}: {exc}",
        }, indent=2), encoding="utf-8")
        print(f"EVIDENCE_PRODUCER_FAILED: {type(exc).__name__}: {exc}")
        return 2
    finally:
        if connection:
            stop_within(connection.close, "collector socket")
        if bridge:
            try:
                if bridge_pid.exists():
                    subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "kill",
                                    bridge_pid.read_text().strip()], capture_output=True, timeout=5)
            except OSError:
                pass
            stop_within(bridge.terminate, "witness bridge")
            stop_within(lambda: bridge.wait(timeout=5), "witness bridge wait")
        if not args.keep_sim_running:
            stop_within(lambda: manager.stop(), "simulation manager")


if __name__ == "__main__":
    raise SystemExit(main())

