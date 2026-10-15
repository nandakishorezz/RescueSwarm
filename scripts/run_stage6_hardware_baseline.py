#!/usr/bin/env python3
"""Collect an immutable read-only Stage 6 hardware baseline."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.common.diagnostics import write_json
from rescue_core.hardware.baseline import HardwareBaselineCollector
from rescue_core.hardware.report import render_stage6_report
from rescue_core.hardware.safety import PhysicalBenchSafetyGuard
from tools.mavlink_probe import probe


def run_directory(root: Path) -> Path:
    run_id = f"{datetime.now().astimezone():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:8]}"
    path = root / run_id
    path.mkdir(parents=True)
    (path / "mount_evidence").mkdir()
    (path / "mount_evidence" / "README.txt").write_text(
        "Place operator-supplied photos of the camera, Pixhawk orientation, GPS placement, labels, and wiring here.\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "hardware_runs")
    parser.add_argument("--output-dir", type=Path, help="Exact new run directory (used by the Stage 6 orchestrator)")
    parser.add_argument("--connection")
    parser.add_argument("--baud", type=int, choices=(57600, 115200, 921600), default=57600)
    parser.add_argument("--heartbeat-timeout", type=float, default=10)
    parser.add_argument("--telemetry-duration", type=float, default=20)
    parser.add_argument("--parameter-timeout", type=float, default=90)
    parser.add_argument("--propellers-removed", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.telemetry_duration <= 30:
        parser.error("--telemetry-duration must be between 10 and 30 seconds")
    if args.output_dir:
        output = args.output_dir
        output.mkdir(parents=True, exist_ok=False)
        (output / "mount_evidence").mkdir()
        (output / "mount_evidence" / "README.txt").write_text(
            "Place operator-supplied photos of physical labels, wiring, and mounting here.\n", encoding="utf-8"
        )
    else:
        output = run_directory(args.output_root)
    collector = HardwareBaselineCollector(output)
    system = collector.collect_host()
    exit_code = 0
    if args.connection:
        PhysicalBenchSafetyGuard().require_propellers_removed(args.propellers_removed)
        result, exit_code = probe(
            args.connection, args.baud, args.heartbeat_timeout, args.telemetry_duration,
            output, collect_parameters=True, parameter_timeout=args.parameter_timeout,
        )
        source = output / "mavlink.json"
        if source.exists():
            source.unlink()
        collector.ingest_mavlink_result(result)
    else:
        reason = "No explicitly selected Pixhawk connection; physical MAVLink was not opened"
        collector.record_mavlink_absent(reason)
    collector.manifest()
    report = render_stage6_report(output)
    (output / "stage6_baseline_report.md").write_text(report, encoding="utf-8")
    (ROOT / "STAGE_6_REPORT.md").write_text(report, encoding="utf-8")
    print(output)
    if not system.get("platform", {}).get("raspberry_pi"):
        print("STAGE 6 SOFTWARE READY - PHYSICAL VALIDATION REQUIRED (this host is not a Raspberry Pi)")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
