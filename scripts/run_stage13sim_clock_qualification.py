"""Disarmed live Phase-2B clock qualification, before any perception gold run.

No extra MAVLink reader, perfect vehicle pose, detector or survivor evaluator.
All acquisition records stream to disk; fitting windows remain bounded.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

import numpy as np

from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber
from rescue_core.geolocation.clock_sync import ClockDomain, ClockSyncEstimator, Timestamp
from rescue_core.simulation.orchestration import SimulationManager
from rescue_core.simulation.perception.camera_source import GazeboCameraSource, _free_loopback_port, _wsl_path
from rescue_core.simulation.perception.clock import SimulationClockMapper
from scripts.run_stage13sim_phase2 import ROOT, build_definition


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sim-seconds", type=float, default=30)
    parser.add_argument("--wall-timeout", type=float, default=240)
    args = parser.parse_args()
    manager = SimulationManager(ROOT / "data/simulation_runs")
    run_dir = manager.prepare(build_definition(args.run_id))
    timesync = ClockSyncEstimator(minimum_samples=12, max_rtt_ms=20, affine=True)
    mapper = SimulationClockMapper(args.run_id, timesync)
    stop = threading.Event()
    source = None
    bridge = None
    connection = None
    camera_thread = None
    camera_errors: list[str] = []
    camera_count = 0
    header_evidence = None
    first_sim = last_sim = None
    transitions = Counter()
    snapshots: deque[dict] = deque(maxlen=200)
    probe_rtts: deque[int] = deque(maxlen=256)
    telemetry_messages = Counter()
    log_lock = threading.Lock()
    evidence_stream = (run_dir / "clock_samples.jsonl").open("w", encoding="utf-8")

    def record(row: dict) -> None:
        with log_lock:
            evidence_stream.write(json.dumps(row, separators=(",", ":")) + "\n")

    try:
        manager.start()
        router = manager.adapter.router
        subscriber = MAVLinkPoseSubscriber(router, clock_sync=timesync)

        def telemetry(message) -> None:
            kind = message.get_type()
            now = time.monotonic_ns()
            if kind == "TIMESYNC" and message.tc1 > 0 and message.ts1 > 0:
                record({"type": "TIMESYNC", "host_send_ns": int(message.ts1), "host_receive_ns": now,
                        "remote_timesync_ns": int(message.tc1)})
            if kind in {"ATTITUDE", "ATTITUDE_QUATERNION", "GLOBAL_POSITION_INT", "LOCAL_POSITION_NED", "SYSTEM_TIME"}:
                boot = int(message.time_boot_ms) * 1_000_000
                telemetry_messages[kind] += 1
                record({"type": kind, "autopilot_boot_ns": boot, "host_receive_ns": now})
                if kind == "GLOBAL_POSITION_INT":
                    mapper.observe_boot(boot, now)

        router.subscribe(telemetry)
        port = _free_loopback_port()
        pid_file = run_dir / "clock_bridge.pid"
        script = ROOT / "rescue_core/simulation/gazebo_bridge/clock_bridge.py"
        bridge = subprocess.Popen([
            "wsl.exe", "-d", "Ubuntu", "--", "python3", _wsl_path(script),
            "--port", str(port), "--pid-file", _wsl_path(pid_file),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=.5)
                connection.settimeout(2)
                break
            except OSError:
                if bridge.poll() is not None:
                    raise RuntimeError(bridge.stderr.read())
                time.sleep(.1)
        if connection is None:
            raise RuntimeError("Clock bridge did not connect")
        reader = connection.makefile("rb")
        source = GazeboCameraSource(str(manager.readiness["camera_topic"]))

        def camera() -> None:
            nonlocal camera_count, header_evidence, first_sim, last_sim
            try:
                source.start()
                while not stop.is_set():
                    frame = source.read()
                    extra = frame.camera_metadata.extra
                    sim_ns = int(extra["sim_capture_time_ns"])
                    if first_sim is None:
                        first_sim = sim_ns
                        header_evidence = {key: extra.get(key) for key in (
                            "capture_timestamp_source", "capture_stamp_sec", "capture_stamp_nsec",
                            "sim_capture_time_ns", "bridge_receive_time_ns", "source_receive_time_ns",
                        )}
                    last_sim = sim_ns
                    camera_count += 1
                    record({"type": "IMAGE_HEADER", "frame_id": frame.frame_id, **extra})
            except Exception as exc:
                if not stop.is_set():
                    camera_errors.append(f"{type(exc).__name__}: {exc}")

        camera_thread = threading.Thread(target=camera, daemon=True)
        camera_thread.start()
        started = time.monotonic()
        next_sync = 0.0
        while time.monotonic() - started < args.wall_timeout:
            now = time.monotonic()
            if now >= next_sync:
                subscriber.request_timesync()
                next_sync = now + .05
            sent = time.monotonic_ns()
            connection.sendall(b"?")
            payload = json.loads(reader.readline(131072))
            received = time.monotonic_ns()
            rtt = received - sent
            probe_rtts.append(rtt)
            record({"type": "BRIDGE_PROBE", "host_send_ns": sent, "host_receive_ns": received, **payload})
            if rtt <= 30_000_000:
                bridge_mid = (payload["bridge_probe_receive_ns"] + payload["bridge_probe_send_ns"]) // 2
                mapper.bridge_to_host.observe(
                    Timestamp(bridge_mid, ClockDomain.BRIDGE_MONOTONIC),
                    Timestamp((sent + received) // 2, ClockDomain.HOST_MONOTONIC),
                    host_received_ns=received, uncertainty_ns=rtt // 2,
                )
                for sample in payload["samples"]:
                    try:
                        host_stamp, fit = mapper.bridge_to_host.map(
                            Timestamp(sample["bridge_receive_ns"], ClockDomain.BRIDGE_MONOTONIC), received,
                        )
                    except ValueError:
                        continue
                    mapper.observe_clock(sample["gazebo_sim_ns"], host_stamp.value_ns, int(fit["uncertainty_ns"]))
            snapshot = mapper.snapshot(received)
            transitions[str(snapshot["quality"])] += 1
            snapshots.append(snapshot)
            record({"type": "MAPPING", **snapshot})
            if camera_errors:
                raise RuntimeError(camera_errors[0])
            if first_sim is not None and last_sim - first_sim >= args.sim_seconds * 1e9:
                break
            time.sleep(.03)
        result = {
            "classification": "CLOCK_QUALIFICATION_ONLY_DISARMED",
            "passed": False, "hardware_touched": False,
            "reason": "Independent one-way transport bias and motion alignment not qualified",
            "image_header_evidence": header_evidence,
            "frames": camera_count, "camera_errors": camera_errors,
            "simulation_duration_s": (last_sim - first_sim) / 1e9 if first_sim is not None else 0,
            "wall_duration_s": time.monotonic() - started,
            "telemetry_source_messages": dict(telemetry_messages),
            "timesync": timesync.diagnostics(),
            "bridge_rtt_p50_ms": float(np.percentile(probe_rtts, 50)) / 1e6 if probe_rtts else None,
            "bridge_rtt_p95_ms": float(np.percentile(probe_rtts, 95)) / 1e6 if probe_rtts else None,
            "quality_samples": dict(transitions),
            "latest_mapping": mapper.snapshot(time.monotonic_ns()),
            "mapping_windows_max_samples": 256,
            "pose_samples_retained": len(subscriber.history.samples),
        }
        (run_dir / "CLOCK_MAPPING_EVIDENCE.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 2
    finally:
        stop.set()
        if source:
            source.stop()
        if camera_thread:
            camera_thread.join(20)
        if connection:
            connection.close()
        if bridge:
            pid_file = run_dir / "clock_bridge.pid"
            if pid_file.exists():
                subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "kill", pid_file.read_text().strip()], capture_output=True, timeout=5)
            bridge.terminate()
            bridge.wait(timeout=5)
        manager.stop()
        evidence_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
