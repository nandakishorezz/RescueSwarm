#!/usr/bin/env python3
"""Generate an evidence-based Stage 4 report without inventing physical or accuracy results."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _metric(section: dict[str, Any] | None, name: str) -> Any:
    return section.get(name) if section else "NOT_TESTED"


def render(run: Path | None) -> str:
    summary = _load(run / "benchmark_summary.json") if run else None
    system = _load(run / "system.json") if run else None
    camera = _load(run / "camera.json") if run else None
    model = _load(run / "model.json") if run else None
    modes = _load(run / "camera_modes.json") if run else None
    physical = bool(system and system.get("raspberry_pi") and camera and camera.get("source_type") == "Picamera2CameraSource")
    camera_status = "PASS" if physical and summary and summary.get("status") == "PASS" else ("FAIL" if physical else "NOT_TESTED")
    real_detector = bool(model and model.get("backend") in {"ncnn", "onnx"})
    performance_pass = bool(
        physical and real_detector and summary and summary.get("detection_output_fps", 0) >= 5
        and (summary.get("end_to_end", {}).get("p95_ms") or float("inf")) < 200
    )
    pipeline_status = "PASS" if summary and summary.get("status") == "PASS" else "NOT_TESTED"
    performance_status = "PASS" if performance_pass else ("PARTIAL" if physical and summary else "NOT_TESTED")
    lines = [
        "# RescueSwarm Stage 4 Report", "", f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}", "",
        "## SYSTEM", "",
        f"- Pi model: {system.get('pi_model', 'NOT_TESTED') if system else 'NOT_TESTED'}",
        f"- RAM: {system.get('ram_total_bytes', 'NOT_TESTED') if system else 'NOT_TESTED'} bytes",
        f"- OS: {system.get('os', 'NOT_TESTED') if system else 'NOT_TESTED'}",
        f"- Kernel: {system.get('kernel', 'NOT_TESTED') if system else 'NOT_TESTED'}", "",
        "## CAMERA", "",
        f"- Physical camera detected: {camera_status}",
        f"- Source: {camera.get('source_type', 'NOT_TESTED') if camera else 'NOT_TESTED'}",
        f"- Sensor/modes: {modes.get('status', 'NOT_TESTED') if modes else 'NOT_TESTED'}",
        f"- Physical capture FPS: {summary.get('capture_fps') if physical and summary else 'NOT_TESTED'}",
        f"- Physical capture stability: {camera_status}",
        f"- Development/replay source FPS: {summary.get('capture_fps', 'NOT_TESTED') if summary else 'NOT_TESTED'}", "",
        "## MODEL", "",
        f"- Model: {model.get('id', 'NOT_TESTED') if model else 'NOT_TESTED'}",
        f"- Role: {model.get('role', 'NOT_TESTED') if model else 'NOT_TESTED'}",
        f"- Hash: {model.get('sha256') or 'NOT_PROVISIONED' if model else 'NOT_TESTED'}",
        f"- License metadata: {model.get('license', 'NOT_TESTED') if model else 'NOT_TESTED'}",
        f"- Backend: {model.get('backend', 'NOT_TESTED') if model else 'NOT_TESTED'}",
        f"- Input size: {model.get('input_size', 'NOT_TESTED') if model else 'NOT_TESTED'}", "",
        "## PERFORMANCE", "",
        f"- Capture FPS: {summary.get('capture_fps', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Detector FPS: {summary.get('detection_output_fps', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Frames captured/inferred/dropped: {summary.get('frames_captured', 'NOT_TESTED') if summary else 'NOT_TESTED'} / {summary.get('frames_inferred', 'NOT_TESTED') if summary else 'NOT_TESTED'} / {summary.get('frames_dropped', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Drop percentage: {summary.get('drop_percent', 'NOT_TESTED') if summary else 'NOT_TESTED'}%",
        f"- Preprocess P50/P95: {_metric(summary.get('preprocess') if summary else None, 'median_ms')} / {_metric(summary.get('preprocess') if summary else None, 'p95_ms')} ms",
        f"- Inference P50/P95: {_metric(summary.get('inference') if summary else None, 'median_ms')} / {_metric(summary.get('inference') if summary else None, 'p95_ms')} ms",
        f"- End-to-end P50/P95/MAX: {_metric(summary.get('end_to_end') if summary else None, 'median_ms')} / {_metric(summary.get('end_to_end') if summary else None, 'p95_ms')} / {_metric(summary.get('end_to_end') if summary else None, 'max_ms')} ms", "",
        "## SYSTEM LOAD", "",
        f"- CPU mean/max: {_metric(summary.get('cpu_percent') if summary else None, 'mean')} / {_metric(summary.get('cpu_percent') if summary else None, 'max')}%",
        f"- RAM RSS mean/max: {_metric(summary.get('process_rss_bytes') if summary else None, 'mean')} / {_metric(summary.get('process_rss_bytes') if summary else None, 'max')} bytes",
        f"- Temperature max: {_metric(summary.get('temperature_c') if summary else None, 'max')} C",
        f"- Thermal throttling: {summary.get('thermal_throttling_detected', 'UNKNOWN') if summary else 'NOT_TESTED'}", "",
        "## DETECTION", "",
        f"- Human detections: {summary.get('human_detections', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Generic person detector: {'FUNCTIONAL' if real_detector and pipeline_status == 'PASS' else 'NOT_TESTED'}",
        "- Accuracy: NOT_MEASURED — LABELLED AERIAL-HUMAN DATASET REQUIRED",
        "- Precision: NOT_MEASURED", "- Recall: NOT_MEASURED", "- Final survivor accuracy validated: NO", "",
        "## STABILITY", "",
        f"- Camera errors: {summary.get('camera_errors', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Detector errors: {summary.get('detector_errors', 'NOT_TESTED') if summary else 'NOT_TESTED'}",
        f"- Memory growth: {summary.get('memory_growth_bytes', 'NOT_TESTED') if summary else 'NOT_TESTED'} bytes",
        f"- Errors: {summary.get('errors', 'NOT_TESTED') if summary else 'NOT_TESTED'}", "",
        "## VERDICT", "",
        f"- CAMERA HARDWARE: {camera_status}", f"- REAL-TIME PIPELINE: {pipeline_status}",
        f"- PERFORMANCE TARGET: {performance_status}",
        f"- GENERIC HUMAN DETECTION: {'FUNCTIONAL' if real_detector and pipeline_status == 'PASS' else 'NOT_TESTED'}",
        "- AERIAL HUMAN ACCURACY: NOT YET VALIDATED", "- FINAL SURVIVOR MODEL: NOT YET BUILT", "",
        f"Artifacts: `{run}`" if run else "Artifacts: no benchmark run selected.", "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "STAGE_4_REPORT.md")
    args = parser.parse_args()
    run = args.run_directory
    if run is None:
        candidates = sorted((ROOT / "data" / "vision_runs").glob("*/benchmark_summary.json"))
        run = candidates[-1].parent if candidates else None
    args.output.write_text(render(run), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
