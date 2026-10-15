#!/usr/bin/env python3
"""Capture a controlled, local physical-person dataset with detector evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.benchmark import BenchmarkRunner, VisionRunWriter
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.evidence import EvidenceRecorder
from rescue_core.vision.factory import create_camera_source, create_detector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "vision.yaml")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--backend", choices=("ncnn", "onnx"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--camera-profile", default="detection")
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--extrinsics-id", required=True)
    parser.add_argument("--scene", required=True, help="Non-identifying scene description")
    parser.add_argument("--subject-pose", choices=("standing", "sitting", "prone", "absent"), required=True)
    parser.add_argument("--truth", type=Path, help="Optional local GroundTruthTarget JSON")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "physical_vision")
    parser.add_argument("--power-evidence", type=Path, required=True)
    args = parser.parse_args()
    power = json.loads(args.power_evidence.read_text(encoding="utf-8"))
    if not power.get("valid_for_precision_measurement"):
        raise RuntimeError("INVALID_POWER_CONDITION: resolve Pi power/thermal flags before capture")
    config = load_vision_config(args.config)
    config.camera.source = "picamera2"
    config.camera.profile = args.camera_profile
    config.detector.backend = args.backend
    config.detector.model_id = args.model
    writer = VisionRunWriter(args.output_root)
    evidence = EvidenceRecorder(
        writer.run_directory, True, config.evidence.confidence_threshold,
        config.evidence.cooldown_s, config.evidence.sample_interval_s,
        config.evidence.max_images,
    )
    summary = BenchmarkRunner(
        create_camera_source(config), create_detector(config, args.backend, args.model), writer,
        config.queue.size, config.benchmark.warmup_frames,
        config.benchmark.resource_sample_interval_s, evidence,
        config.camera.restart_attempts, config.camera.restart_backoff_s,
    ).run(args.duration, "end_to_end")
    truth = json.loads(args.truth.read_text(encoding="utf-8")) if args.truth else None
    writer.json("physical_session.json", {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "physical_camera": True, "physical_flight": False,
        "calibration_id": args.calibration_id, "extrinsics_id": args.extrinsics_id,
        "scene": args.scene, "subject_pose": args.subject_pose,
        "ground_truth": truth, "personal_information_collected": False,
        "power_evidence": str(args.power_evidence),
        "accuracy_scope": "BENCH_PERSON_DETECTION_NOT_AERIAL_RECALL",
    })
    print(writer.run_directory)
    return 0 if summary.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
