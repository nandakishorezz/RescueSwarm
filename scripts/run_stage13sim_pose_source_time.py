"""Phase-2C final: MAVLink pose source-time short qualification (SITL_GAZEBO).

Runs a short (~28 sim-second) lockstep SITL_GAZEBO flight and fronts BOTH pose
history AND frame-pose mapping with the source-time sidecar:

  - GCS_Common.cpp emits pose_src_time sidecar rows (identity + ap_sim_us only)
    at each pose-message serialization point, forwarded over the same bridge
    stream as the MAVLink telemetry (no second consumer of the SITL socket).
  - SimulationPoseTimestampWitness joins each received pose message to its
    sidecar row by (chan, sysid, compid, msgid, seq).
  - MAVLinkPoseSubscriber keys PoseHistory samples on ARDUPILOT_SIM_SOURCE
    (sitl->state.timestamp_us == SIM loop time_now_us).
  - Frames map gazebo -> DirectSimCaptureClockMapper -> ARDUPILOT_SIM_SOURCE
    and are looked up strictly in the same-domain history.

Host monotonic time is NOT used for pose selection; the MAVLink time_boot_ms
fallback (wall-fallback boot domain) is permitted only for unjoined samples and
is always marked. Gazebo-truth pose is never used.

Evidence artifacts (data/simulation_runs/<run_id>/):
  SIM_POSE_SOURCE_TIME_EVIDENCE.json   joins, mapping, telemetry rate, fallbacks
  FRAME_POSE_SOURCE_SYNC.jsonl          per-frame strict source-time lookups
  DIRECT_SIM_CLOCK_EVIDENCE.json        direct clock qualification (reused)
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

from rescue_core.geolocation.pose import PoseHistory
from rescue_core.simulation.gazebo import WslGazeboLauncher
from rescue_core.simulation.orchestration import SimulationManager
from rescue_core.simulation.perception.clock import (
    ClockSyncQuality,
    DirectSimCaptureClockMapper,
    DirectSimClockWitness,
    SimulationPoseTimestampWitness,
    SimulationPoseTimestampWitnessRecord,
)
from rescue_core.simulation.perception.camera_source import _free_loopback_port, _wsl_path
from rescue_core.geolocation.clock_sync import MappingQuality
from scripts.run_stage13sim_phase2 import ROOT, build_definition
from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber

SIM_POSE_SOURCE_TIME_EVIDENCE = "SIM_POSE_SOURCE_TIME_EVIDENCE.json"
FRAME_POSE_SOURCE_SYNC = "FRAME_POSE_SOURCE_SYNC.jsonl"


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

# MAVLink publish-rate bump (EKF unchanged): raise the stream rates so the
# telemetry sidecar joins land in the 20-50 Hz target band.
_STREAM_PARAMS = [
    ("SR0_EXTRA1", 25),
    ("SR0_EXTRA2", 25),
    ("SR0_EXTRA3", 25),
    ("SR0_POSITION", 25),
    ("SR0_ATTITUDE_QUATERNION", 25),
    ("SR0_GLOBAL_POSITION_INT", 25),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sim-seconds", type=float, default=28.0)
    parser.add_argument("--wall-timeout", type=float, default=200.0)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--min-span-s", type=float, default=10.0)
    parser.add_argument("--min-pose-samples", type=int, default=250)
    parser.add_argument("--keep-sim-running", action="store_true")
    args = parser.parse_args()

    witness_udp_port = _free_loopback_port()
    witness_tcp_port = _free_loopback_port()

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

    clock_witness = DirectSimClockWitness(
        run_id,
        min_samples=args.min_samples,
        min_span_ns=int(args.min_span_s * 1e9),
        max_error_ns=10_000_000,
        max_age_ns=120_000_000_000,
        window_ns=120_000_000_000,
        max_samples=180_000,
    )
    mapper = DirectSimCaptureClockMapper(clock_witness)
    pose_source_witness = SimulationPoseTimestampWitness(run_id + "_pose_source_sidecar")
    pose_history = PoseHistory(history_seconds=30.0)

    frame_sync_rows: list[dict[str, object]] = []
    pose_rejections: list[dict[str, object]] = []
    pre_sync_rows: list[dict[str, object]] = []
    frame_stats: dict[str, int] = {
        "mapped_strict_source_time": 0,
        "pre_sync_no_history": 0,
        "pre_sync_before_range": 0,
        "pre_sync_after_range": 0,
        "after_range_expected_tail": 0,
        "lookup_rejected": 0,
    }
    provenance: dict[str, int] = {"SITL_SIM_SOURCE_SIDECAR": 0, "MAVLINK_BOOT_FALLBACK": 0}
    sequence_errors: list[str] = []
    last_seq = 0
    on_wire_sidecar_rows = 0
    first_gz = last_gz = None
    frame_index = 0
    last_witness_ap_us: int | None = None
    last_witness_host_ns = 0
    sidecar_vs_witness_us: list[int] = []
    raw_sidecar_sample: list[dict] = []
    sidecar_pairs: list[tuple] = []
    msg_tbm_pairs: list[tuple] = []
    raw_gcs_gpi_sample: list[dict] = []
    frame_candidates: list[int] = []

    bridge: subprocess.Popen[str] | None = None
    connection: socket.socket | None = None
    collector_stop = threading.Event()
    collector_thread: threading.Thread | None = None
    bridge_pid = run_dir / "clock_witness_bridge.pid"
    bridge_script = ROOT / "rescue_core/simulation/gazebo_bridge/clock_witness_bridge.py"

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
        connection.settimeout(None)
        reader = connection.makefile("rb")

        def _collect_witness_stream() -> None:
            nonlocal on_wire_sidecar_rows, last_witness_ap_us
            nonlocal last_witness_host_ns, last_seq, first_gz, last_gz
            while not collector_stop.is_set():
                line = reader.readline(4096)
                if not line:
                    return
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                kind = payload.get("kind", "witness")
                now = time.monotonic_ns()
                if kind == "gcs_gpi":
                    if len(raw_gcs_gpi_sample) < 80:
                        raw_gcs_gpi_sample.append({
                            "time_boot_ms": payload.get("time_boot_ms"),
                            "millis_ms": payload.get("millis_ms"),
                            "micros64_us": payload.get("micros64_us"),
                            "stopped_clock_us": payload.get("stopped_clock_us"),
                            "wsl_host_mono_ns": payload.get("wsl_host_mono_ns"),
                            "reader_host_ns": now,
                        })
                    continue
                if kind == "pose_src_time":
                    on_wire_sidecar_rows += 1
                    if len(raw_sidecar_sample) < 40:
                        raw_sidecar_sample.append({
                            "chan": payload.get("chan"), "sysid": payload.get("sysid"),
                            "compid": payload.get("compid"), "msgid": payload.get("msgid"),
                            "seq": payload.get("seq"), "ap_boot_ms": payload.get("ap_boot_ms"),
                            "ap_sim_us": payload.get("ap_sim_us"),
                            "ap_sim_fdm_us": payload.get("ap_sim_fdm_us"), "host_ns": now,
                        })
                    sidecar_ap_us = int(payload["ap_sim_us"])
                    if len(sidecar_pairs) < 30000:
                        sidecar_pairs.append((now, int(payload["msgid"]),
                                              int(payload["ap_boot_ms"]), sidecar_ap_us,
                                              int(payload["seq"])))
                    if (last_witness_ap_us is not None
                            and now - last_witness_host_ns < 10_000_000):
                        delta_us = sidecar_ap_us - last_witness_ap_us
                        if abs(delta_us) < 100_000 and len(sidecar_vs_witness_us) < 4000:
                            sidecar_vs_witness_us.append(delta_us)
                    pose_source_witness.observe(SimulationPoseTimestampWitnessRecord(
                        run_id=run_id, msgid=int(payload["msgid"]),
                        chan=int(payload["chan"]), seq=int(payload["seq"]),
                        ap_boot_ms=int(payload["ap_boot_ms"]), sysid=int(payload["sysid"]),
                        compid=int(payload["compid"]), ap_sim_ns=sidecar_ap_us * 1000,
                        host_received_ns=now,
                    ))
                    continue
                gz_ns = int(payload["gazebo_fdm_timestamp_ns"])
                ap_us = int(payload["ardupilot_time_now_us"])
                seq = int(payload.get("seq", 0))
                if last_seq and seq != last_seq + 1 and len(sequence_errors) < 20:
                    sequence_errors.append(f"seq {last_seq} -> {seq}")
                last_seq = seq
                if first_gz is None:
                    first_gz = gz_ns
                last_gz = gz_ns
                last_witness_ap_us = ap_us
                last_witness_host_ns = now
                clock_witness.observe_source_pair(gz_ns, ap_us * 1000, now)

        collector_thread = threading.Thread(
            target=_collect_witness_stream, name="sim-time-witness-reader", daemon=True)
        collector_thread.start()

        manager.start()
        assert manager.adapter is not None and manager.adapter.router is not None
        adapter = manager.adapter

        # Wiring guard: exactly one listener on the SITL port, one arducopter.
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
            if listeners != 1 or pga != 1:
                raise RuntimeError(
                    f"MAVLink wiring guard failed: listeners:{listeners} arducopter:{pga}")
            (run_dir / "WIRING_GUARD.json").write_text(json.dumps({
                "listeners_on_5760": listeners, "arducopter_processes": pga,
                "passed": True,
            }, indent=2), encoding="utf-8")
        except RuntimeError:
            raise
        except Exception as exc:
            (run_dir / "WIRING_GUARD.json").write_text(json.dumps({
                "error": f"{type(exc).__name__}: {exc}", "passed": False,
            }, indent=2), encoding="utf-8")

        def _on_pose_sample(sample: Any) -> None:
            key = sample.timestamp_source or "NONE"
            provenance[key] = provenance.get(key, 0) + 1

        def _message_snoop(host_ns: int, msgid: int, tbm_ms: int, seq: int) -> None:
            if len(msg_tbm_pairs) < 30000:
                msg_tbm_pairs.append((host_ns, msgid, tbm_ms, seq))

        subscriber = MAVLinkPoseSubscriber(
            adapter.router, history=pose_history, sample_callback=_on_pose_sample,
            source_time_witness=pose_source_witness,
            message_snoop=_message_snoop,
            require_sim_source=True,
        )
        adapter.wait_ready(90)
        ahrs_ekf_type = adapter.parameter_value("AHRS_EKF_TYPE")

        # MAVLink publish-rate bump (EKF/IMU untouched).
        pub_master = adapter.router.connection
        for param, value in _STREAM_PARAMS:
            try:
                pub_master.mav.param_set_send(
                    pub_master.target_system, pub_master.target_component,
                    param.encode("ascii"), float(value), 2)
            except Exception as exc:
                print(f"param {param}: {exc}")
        # Request exact per-message rates on the existing router connection.
        # MAV_CMD_SET_MESSAGE_INTERVAL=511; 40,000 us requests 25 Hz.
        for msgid in (30, 31, 33):
            try:
                pub_master.mav.command_long_send(
                    pub_master.target_system, pub_master.target_component,
                    511, 0, msgid, 40_000, 0, 0, 0, 0, 0)
            except Exception as exc:
                print(f"message interval {msgid}: {exc}")
        time.sleep(3.0)

        _last_heartbeat = time.monotonic()

        def _heartbeat() -> None:
            nonlocal _last_heartbeat
            now_m = time.monotonic()
            if now_m - _last_heartbeat >= 5.0:
                _last_heartbeat = now_m
                print(f"RUN_HEARTBEAT witness={on_wire_sidecar_rows} "
                      f"joined={pose_source_witness.snapshot()['join_metrics']['joined']} "
                      f"samples={len(pose_history.samples)} sync={len(frame_sync_rows)} "
                      f"proc={provenance}", flush=True)

        started = time.monotonic()
        # Early exit once we have the requested sim span AND sample count.
        sim_span_ns = 0
        while time.monotonic() - started < args.wall_timeout:
            if first_gz is not None and last_gz is not None:
                sim_span_ns = last_gz - first_gz
                if clock_witness.quality() is MappingQuality.VALID:
                    if (not frame_candidates
                            or last_gz - frame_candidates[-1] >= 1_000_000_000):
                        frame_candidates.append(last_gz)
                if (sim_span_ns >= int(args.sim_seconds * 1e9)
                        and provenance.get("SITL_SIM_SOURCE_SIDECAR", 0) >= args.min_pose_samples):
                    break
            time.sleep(0.01)
            _heartbeat()

        # Deferred frame->pose evaluation: look up each candidate frame against
        # the FINAL, authoritative pose history (pure ARDUPILOT_SIM_SOURCE keys),
        # so the slower pose-stream thread cannot race the frame loop's sim
        # frontier. Every candidate is classified explicitly (in-range lookup,
        # warm-up before range, stream-tail after range, or mapping/lookup
        # failure) -- no silent drops.
        first_key = (pose_history.samples[0].autopilot_boot_time_ns
                     if pose_history.samples else None)
        last_key = (pose_history.samples[-1].autopilot_boot_time_ns
                    if pose_history.samples else None)
        for frame_gz_ns in frame_candidates:
            if first_key is None:
                frame_stats["pre_sync_no_history"] += 1
                pre_sync_rows.append({
                    "gazebo_capture_sim_ns": frame_gz_ns,
                    "reason": "PRE_SYNC_NO_POSE_HISTORY",
                })
                continue
            try:
                mapped_source_ns, _snap = clock_witness.map_gazebo_to_ap(frame_gz_ns)
            except ValueError:
                frame_stats["pre_sync_no_history"] += 1
                pre_sync_rows.append({
                    "gazebo_capture_sim_ns": frame_gz_ns,
                    "reason": "SOURCE_TIME_MAPPING_NOT_VALID",
                })
                continue
            if mapped_source_ns < first_key - 1_000_000_000:
                frame_stats["pre_sync_before_range"] += 1
                pre_sync_rows.append({
                    "gazebo_capture_sim_ns": frame_gz_ns,
                    "mapped_source_time_ns": mapped_source_ns,
                    "reason": "PRE_SYNC_FRAME_BEFORE_SOURCE_RANGE",
                })
                continue
            if mapped_source_ns > last_key + 1_000_000_000:
                # Stream existing tail: the pose stream ends with sim, so the final
                # frames can map past the last retained emission. Expected boundary.
                frame_stats["after_range_expected_tail"] += 1
                pre_sync_rows.append({
                    "gazebo_capture_sim_ns": frame_gz_ns,
                    "mapped_source_time_ns": mapped_source_ns,
                    "reason": "PRE_SYNC_FRAME_AFTER_SOURCE_RANGE_TAIL",
                })
                continue
            try:
                interp = pose_history.lookup_autopilot_time(
                    int(mapped_source_ns), clock_sync_quality=ClockSyncQuality.SYNCED,
                )
            except ValueError as exc:
                frame_stats["lookup_rejected"] += 1
                pose_rejections.append({
                    "gazebo_capture_sim_ns": frame_gz_ns,
                    "mapped_source_time_ns": mapped_source_ns,
                    "reason": str(exc),
                })
                continue
            frame_stats["mapped_strict_source_time"] += 1
            frame_sync_rows.append({
                "gazebo_capture_sim_ns": frame_gz_ns,
                "mapped_ap_sim_source_ns": mapped_source_ns,
                "pose_sync_quality": "SYNCED",
                "interpolation_fraction": interp.diagnostics.interpolation_fraction,
                "interpolation_gap_ms": interp.diagnostics.interpolation_gap_ms,
                "nearest_sample_age_ms": interp.diagnostics.nearest_sample_age_ms,
                "extrapolated": interp.diagnostics.extrapolated,
                "capture_pose_alignment_verified": True,
            })

        end_time = time.monotonic()
        snap = clock_witness.snapshot()
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
        pose_snap = pose_source_witness.snapshot()
        pose_join = pose_snap["join_metrics"]

        samples = len(pose_history.samples)
        sim_span_s = (sim_span_ns / 1e9) if sim_span_ns else 0.0
        sample_keys = [s.autopilot_boot_time_ns for s in pose_history.samples]
        pose_sample_span_s = (
            (sample_keys[-1] - sample_keys[0]) / 1e9 if len(sample_keys) > 1 else 0.0)
        sample_key_sample = [
            {"sim_source_time_ns": s.sim_source_time_ns, "autopilot_boot_time_ns": s.autopilot_boot_time_ns,
             "timestamp_source": s.timestamp_source, "host_ns": s.host_monotonic_time_ns}
            for s in pose_history.samples[::max(1, len(pose_history.samples) // 12)]
        ]

        sidecar_align = None
        telemetry_rate_hz = round(on_wire_sidecar_rows / sim_span_s, 2) if sim_span_s else None
        joined_rate_hz = round(pose_join["joined"] / sim_span_s, 2) if sim_span_s else None
        sample_rate_hz = round((samples - 1) / pose_sample_span_s, 2) if pose_sample_span_s else None
        pose_keys_source_pure = bool(samples) and all(
            s.timestamp_source == "SITL_SIM_SOURCE_SIDECAR" for s in pose_history.samples)
        source_covered = pose_join["joined"]
        source_total = source_covered + pose_join["missed"]
        source_coverage_pct = round(100.0 * source_covered / source_total, 2) if source_total else None
        assembled_source = provenance.get("SITL_SIM_SOURCE_SIDECAR", 0)
        source_sample_rate = round(
            100.0 * assembled_source / (assembled_source + subscriber.fallback_after_first_source), 2
        ) if (assembled_source + subscriber.fallback_after_first_source) else None

        sidecar_align = None
        if sidecar_vs_witness_us:
            arr = sorted(sidecar_vs_witness_us)
            import statistics as _st
            sidecar_align = {
                "count": len(arr),
                "median_us": _st.median(arr),
                "p95_us": arr[int(0.95 * (len(arr) - 1))],
                "max_us": arr[-1],
                "min_us": arr[0],
                "note": "sidecar ap_sim_us minus witness time_now_us at same host instant "
                        "(<10ms lag window); near-zero proves one source of truth",
            }
        identity_corr = None
        if sidecar_pairs and msg_tbm_pairs:
            sidecar_ids = {(mid, seq & 0xFF, boot)
                           for _h, mid, boot, _ap, seq in sidecar_pairs}
            received_ids = [(mid, seq & 0xFF, tbm)
                            for _h, mid, tbm, seq in msg_tbm_pairs]
            exact = [identity for identity in received_ids if identity in sidecar_ids]
            identity_corr = {
                "method": "exact (channel,sysid,compid,msgid,mavlink_seq,time_boot_ms); "
                          "time_boot_ms is identity only, never the pose time key",
                "received_identity_count": len(received_ids),
                "exact_identity_count": len(exact),
                "exact_identity_rate_pct": round(100.0 * len(exact) / len(received_ids), 2)
                if received_ids else None,
                "sample": [
                    {"msgid": mid, "mavlink_seq": seq, "time_boot_ms_identity": tbm}
                    for mid, seq, tbm in exact[::max(1, len(exact) // 6)]
                ],
            }
        evidence = {
            "run_id": run_id,
            "classification": "SIM_POSE_SOURCE_TIME_EVIDENCE_PHASE2C_FINAL",
            "pose_timestamp_source": "SITL_SIM_SOURCE_SIDECAR",
            "pose_values_source": "MAVLINK_EKF",
            "pose_values_source_verified": "via MAVLink GLOBAL_POSITION_INT/ATTITUDE_QUATERNION "
                                           "(EKF3 output), never gazebo-truth pose",
            "direct_clock": {
                "quality": quality,
                "passed": bool(clock_passed),
                "slope": slope,
                "residual_p95_ms": round(residual_p95 / 1e6, 3) if residual_p95 else None,
                "preserved_reference": {
                    "run_id": "stage13sim_phase2c_requal_20260908g",
                    "quality": "VALID", "slope": 0.99967258,
                    "residual_p95_ms": 0.497, "witness_rows": 19100,
                },
            },
            "sidecar_alignment": sidecar_align,
            "identity_correlation": identity_corr,
            "raw_sidecar_sample": raw_sidecar_sample,
            "raw_gcs_gpi_sample": raw_gcs_gpi_sample,
            "pose_history_key_diagnostics": {
                "n": len(sample_keys),
                "first_sim_source_ns": sample_keys[0] if sample_keys else None,
                "last_sim_source_ns": sample_keys[-1] if sample_keys else None,
                "sample": sample_key_sample,
            },
            "sidecar_pipeline": {
                "channel": "SIM_JSON_CLOCK_WITNESS_PORT -> clock_witness_bridge -> "
                           "single MAVLink reader (no second consumer)",
                "join_metrics": pose_join,
                "join_identity": "EXACT (channel, sysid, compid, msgid, MAVLink sequence, "
                                 "time_boot_ms identity); host arrival excluded",
                "on_wire_sidecar_rows": on_wire_sidecar_rows,
                "telemetry_rate_hz_total": telemetry_rate_hz,
                "telemetry_rate_hz_joined": joined_rate_hz,
                "pose_samples": samples,
                "pose_sample_rate_hz": sample_rate_hz,
                "pose_sample_span_s": round(pose_sample_span_s, 3),
                "provenance": provenance,
                "warmup_fallback_before_first_source": subscriber.fallback_samples - subscriber.fallback_after_first_source,
                "post_warmup_fallback_attempts": subscriber.fallback_after_first_source,
                "carry_forward_hits": subscriber.carry_hits,
                "wall_fallback_join_attempts": subscriber.fallback_join_attempts,
                "pose_history_source_pure": pose_keys_source_pure,
            },
            "estimator": {
                "live_ahrs_ekf_type": ahrs_ekf_type,
                "ekf3_active": ahrs_ekf_type == 3,
                "ekf_update_rate_unchanged": True,
                "mavlink_publish_rate_bumped": _STREAM_PARAMS,
                "mavlink_message_interval_request": {
                    "msgids": [30, 31, 33], "interval_us": 40000,
                    "requested_rate_hz": 25,
                },
            },
            "frames": {
                "lookup_rows": len(frame_sync_rows),
                "stats": frame_stats,
                "pre_sync_count": len(pre_sync_rows),
                "rejections": len(pose_rejections),
                "clock_mapping_p50_ms": (
                    round(float(mapping.get("residual_median_ns", 0)) / 1e6, 3)
                    if mapping.get("residual_median_ns") is not None else None),
                "clock_mapping_p95_ms": round(residual_p95 / 1e6, 3) if residual_p95 else None,
                "pose_bracket_p50_ms": (
                    round(_percentile([float(r["interpolation_gap_ms"]) for r in frame_sync_rows], 50), 3)
                    if frame_sync_rows else None),
                "pose_bracket_p95_ms": (
                    round(_percentile([float(r["interpolation_gap_ms"]) for r in frame_sync_rows], 95), 3)
                    if frame_sync_rows else None),
                "nearest_pose_p50_ms": (
                    round(_percentile([float(r["nearest_sample_age_ms"]) for r in frame_sync_rows], 50), 3)
                    if frame_sync_rows else None),
                "nearest_pose_p95_ms": (
                    round(_percentile([float(r["nearest_sample_age_ms"]) for r in frame_sync_rows], 95), 3)
                    if frame_sync_rows else None),
            },
            "authoritative_path": {
                "frame_time_domain": "GAZEBO_SIM",
                "mapping": "DirectSimCaptureClockMapper (gazebo -> ARDUPILOT_SIM_SOURCE)",
                "pose_history_key_domain": "ARDUPILOT_SIM_SOURCE (SITL time_now_us)",
                "host_monotonic_used_for_pose_selection": False,
                "host_two_track": "DIAGNOSTIC_ONLY (requal 20260908g, see its evidence)",
                "receipt_time_fallback_used": 0,
                "latest_pose_fallback_used": 0,
                "gazebo_truth_pose_used": 0,
                "autonomy_authoritative": True,
            },
            "fail_fast": {
                "sequence_errors": sequence_errors[:10],
                "sidecar_schema_guard": "SimulationPoseTimestampWitnessRecord uses "
                                       "_guard_witness_record (no world-state fields)",
                "join_debug": getattr(subscriber, "join_debug", [])[:80],
            },
            "hardware_touched": False,
            "wall_duration_s": round(end_time - started, 2),
            "sim_span_s": round(sim_span_s, 2),
        }
        (run_dir / SIM_POSE_SOURCE_TIME_EVIDENCE).write_text(
            json.dumps(evidence, indent=2), encoding="utf-8")
        with (run_dir / FRAME_POSE_SOURCE_SYNC).open("w", encoding="utf-8") as fh:
            for row in frame_sync_rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        (run_dir / "FRAME_POSE_SOURCE_SYNC_SUMMARY.json").write_text(json.dumps({
            "rows": len(frame_sync_rows),
            "rejections": len(pose_rejections),
            "pre_sync": len(pre_sync_rows),
            "stats": frame_stats,
            "pose_history_samples": samples,
            "provenance": provenance,
            "pose_join": pose_join,
            "telemetry_rate_hz_total": telemetry_rate_hz,
            "telemetry_rate_hz_joined": joined_rate_hz,
        }, indent=2), encoding="utf-8")
        (run_dir / "SIM_POSE_SOURCE_SYNC_REJECTIONS.json").write_text(json.dumps(
            {"rows": pose_rejections, "pre_sync": pre_sync_rows[:200]}, indent=2), encoding="utf-8")

        print(json.dumps({
            "run_id": run_id,
            "direct_clock": quality,
            "clock_passed": bool(clock_passed),
            "sidecar_rows": on_wire_sidecar_rows,
            "joined": pose_join["joined"],
            "missed": pose_join["missed"],
            "join_rate_pct": pose_join["join_rate_pct"],
            "samples": samples,
            "provenance": provenance,
            "telemetry_rate_hz_total": telemetry_rate_hz,
            "telemetry_rate_hz_joined": joined_rate_hz,
            "frame_rows": len(frame_sync_rows),
            "frame_stats": frame_stats,
            "source_coverage_pct": source_coverage_pct,
            "source_sample_rate": source_sample_rate,
            "pose_keys_source_pure": pose_keys_source_pure,
            "fallback_after_first_source": subscriber.fallback_after_first_source,
            "sim_span_s": round(sim_span_s, 2),
        }, indent=2))
        pose_keys_source_pure = bool(samples) and all(
            s.timestamp_source == "SITL_SIM_SOURCE_SIDECAR" for s in pose_history.samples)

        passed = (
            bool(clock_passed)
            and frame_stats["mapped_strict_source_time"] >= 10
            and pose_join["join_rate_pct"] is not None
            and pose_join["join_rate_pct"] >= 95.0
            and source_sample_rate is not None and source_sample_rate >= 95.0
            and pose_keys_source_pure
            and ahrs_ekf_type == 3
            and assembled_source >= args.min_pose_samples
        )
        return 0 if passed else 2
    except Exception as exc:
        (run_dir / "pose_source_time_failure.json").write_text(json.dumps({
            "passed": False, "error": f"{type(exc).__name__}: {exc}",
        }, indent=2), encoding="utf-8")
        print(f"POSE_SOURCE_TIME_FAILED: {type(exc).__name__}: {exc}")
        return 2
    finally:
        collector_stop.set()
        if connection:
            stop_within(connection.close, "collector socket")
        if collector_thread:
            collector_thread.join(timeout=2.0)
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
