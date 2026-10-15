"""Live Phase-2C direct simulation clock qualification (SITL_GAZEBO).

Goal: fit T_ap = a*T_gazebo + b from SAME-UPDATE source-domain pairs emitted
by the SIM_JSON clock witness patch, with no host-arrival coupling.

The witness pairs originate inside SIM_JSON::recv_fdm where both
state.timestamp_s (Gazebo) and time_now_us (AP) exist in the same FDM update.
A WSL-local bridge relays datagrams to this collector over loopback TCP.

Artifact: data/simulation_runs/<run>/DIRECT_SIM_CLOCK_EVIDENCE.json

Gates:
  - quality must reach VALID (>=min_samples pairs, >=min_span_s sim coverage)
  - residual P95 <= 10 ms (preferred <= 5 ms)
  - slope within [0.999, 1.001] (lockstep slope is 1 by construction)
  - no physics-reset detections, no monotonicity inversions in the window
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import subprocess
import time

from rescue_core.geolocation.clock_sync import MappingQuality
from rescue_core.simulation.gazebo import WslGazeboLauncher
from rescue_core.simulation.orchestration import SimulationManager
from rescue_core.simulation.perception.clock import DirectSimClockWitness
from rescue_core.simulation.perception.camera_source import _free_loopback_port, _wsl_path
from scripts.run_stage13sim_phase2 import ROOT, build_definition


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
        max_error_ns=10_000_000,   # P95 <= 10 ms hard gate
        max_age_ns=120_000_000_000,
        window_ns=120_000_000_000,
        max_samples=4000,          # witness emits at 200 Hz → 20 s coverage
    )
    bridge: subprocess.Popen[str] | None = None
    connection: socket.socket | None = None
    bridge_pid = run_dir / "clock_witness_bridge.pid"
    bridge_script = ROOT / "rescue_core/simulation/gazebo_bridge/clock_witness_bridge.py"
    sequence_errors: list[str] = []
    last_seq = 0
    witness_count = 0
    reset_seen = Counter()

    def start_sim() -> None:
        manager.start()
        assert manager.adapter is not None
        manager.adapter.wait_ready(90)

    def stop_within(fn, label: str, timeout_s: float = 6.0) -> None:
        try:
            fn()
        except Exception as exc:  # teardown must never mask the result
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
                connection.settimeout(2.0)
                break
            except OSError:
                time.sleep(0.2)
        if connection is None:
            raise RuntimeError("could not connect to witness bridge")
        connection.settimeout(5.0)
        reader = connection.makefile("rb")

        start_sim()
        started = time.monotonic()
        first_gz = last_gz = None
        while time.monotonic() - started < args.wall_timeout:
            line = reader.readline(4096)
            if not line:
                # ArduPilot may not have emitted yet; keep polling.
                time.sleep(0.05)
                continue
            try:
                payload = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            gz_ns = int(payload["gazebo_fdm_timestamp_ns"])
            ap_us = int(payload["ardupilot_time_now_us"])
            ap_ns = ap_us * 1000
            seq = int(payload.get("seq", 0))
            now = time.monotonic_ns()
            if last_seq and seq != last_seq + 1:
                if len(sequence_errors) < 20:
                    sequence_errors.append(f"seq {last_seq} -> {seq}")
            last_seq = seq
            # A physics reset in Gazebo would rewind the sim clock to an epoch
            # before the current window; fail if the witness itself detects it.
            if gz_ns < (last_gz or 0):
                reset_seen["sim_clock_rewind"] += 1
            if first_gz is None:
                first_gz = gz_ns
            last_gz = gz_ns
            witness.observe_source_pair(gz_ns, ap_ns, now)
            witness_count += 1
            if witness_count % 100 == 0:
                q = witness.quality()
                if q is MappingQuality.VALID and last_gz - first_gz >= args.sim_seconds * 1e9:
                    break
            time.sleep(0.001)
        end_time = time.monotonic()

        snap = witness.snapshot()
        mapping = snap["mapping"]
        quality = snap["quality"]
        residual_p95 = mapping.get("residual_p95_ns")
        residual_p50 = mapping.get("residual_p50_ns")
        slope = mapping.get("slope")
        passed = (
            quality == MappingQuality.VALID.value
            and residual_p95 is not None and residual_p95 <= 10_000_000
            and slope is not None and 0.999 <= slope <= 1.001
            and not sequence_errors
            and sum(reset_seen.values()) == 0
        )
        result = {
            "run_id": run_id,
            "classification": "DIRECT_SIM_CLOCK_QUALIFICATION_PHASE2C",
            "proof_source": "SIM_JSON_SAME_UPDATE_SOURCE_DOMAIN_PAIRS",
            "witness_stream": "SIM_JSON_CLOCK_WITNESS_PATCH",
            "patch": {
                "file": "simulation/patches/0001-sitl-json-clock-witness.patch",
                "ardupilot_base": "af2a1bafc8ab993418e6714cbc2674dcc178d11a",
            },
            "witness_samples": witness_count,
            "quality": quality,
            "passed": bool(passed),
            "no_host_arrival_pairing": True,
            "source_domain_capture": "recv_fdm::state.timestamp_s + time_now_us",
            "pair_generation_rate_hz": round(witness_count / max(0.001, args.sim_seconds), 1),
            "fit": {
                "slope": slope,
                "offset_ns": mapping.get("offset_ns"),
                "residual_p50_ns": residual_p50,
                "residual_p95_ns": residual_p95,
                "maximum_residual_ns": mapping.get("maximum_residual_ns"),
                "uncertainty_ns": mapping.get("uncertainty_ns"),
                "fit_outliers": mapping.get("fit_outliers"),
            },
            "sim_span_ns": int(last_gz - first_gz) if first_gz is not None and last_gz is not None else None,
            "observations": dict(reset_seen),
            "sequence_errors": sequence_errors[:10],
            "wall_duration_s": round(end_time - started, 2),
            "hardware_touched": False,
            "confidence": "RESIDUAL",
        }
        (run_dir / "DIRECT_SIM_CLOCK_EVIDENCE.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if passed else 2
    except Exception as exc:
        (run_dir / "phase2c_clock_qualification_failure.json").write_text(json.dumps({
            "passed": False, "error": f"{type(exc).__name__}: {exc}",
        }, indent=2), encoding="utf-8")
        print(f"QUALIFICATION_FAILED: {type(exc).__name__}: {exc}")
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