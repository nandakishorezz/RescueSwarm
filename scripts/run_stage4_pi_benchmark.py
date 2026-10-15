#!/usr/bin/env python3
"""Run guarded Pi 5 camera bring-up and three-phase Stage 4 benchmarking."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.benchmark import BenchmarkRunner, VisionRunWriter
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.evidence import EvidenceRecorder
from rescue_core.vision.factory import create_camera_source, create_detector
from rescue_core.vision.system_metrics import system_snapshot
from tools.generate_stage4_report import render
from tools.list_camera_modes import list_modes, validate_profile_against_modes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "vision.yaml")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--model")
    parser.add_argument("--backend", choices=("ncnn", "onnx", "mock"))
    parser.add_argument("--camera-profile", default="detection")
    parser.add_argument("--save-samples", action="store_true")
    args = parser.parse_args()
    system = system_snapshot()
    if not system["raspberry_pi"]:
        (ROOT / "STAGE_4_REPORT.md").write_text(render(None), encoding="utf-8")
        print("PHYSICAL VISION NOT_TESTED: run this command on the Raspberry Pi 5", file=sys.stderr)
        return 0
    config = load_vision_config(args.config)
    config.camera.source = "picamera2"
    config.camera.profile = args.camera_profile
    if args.backend:
        config.detector.backend = args.backend
    if args.model:
        config.detector.model_id = args.model
    modes = list_modes()
    if modes["status"] != "PASS":
        print(f"CAMERA ENUMERATION FAILED: {modes['reason']}", file=sys.stderr)
        print("Run Stage 1 camera diagnostics and tools/prepare_ov9281_config.py --dry-run before retrying.")
        return 1
    profile = config.camera.profiles[config.camera.profile]
    supported, reason = validate_profile_against_modes(modes, profile.width, profile.height, profile.fps)
    if not supported:
        print(f"CAMERA PROFILE INVALID: {reason}", file=sys.stderr)
        print("Update config/vision.yaml from camera_modes.json; no capture was attempted.")
        return 1
    print(reason)

    phase_duration = min(args.duration, 60)
    capture_writer = VisionRunWriter(config.runtime.run_root)
    capture_writer.json("camera_modes.json", modes)
    capture_evidence = EvidenceRecorder(
        capture_writer.run_directory, True, sample_interval_s=max(0.2, phase_duration / 20), max_images=50
    )
    capture = BenchmarkRunner(
        create_camera_source(config), None, capture_writer,
        config.queue.size, 0, config.benchmark.resource_sample_interval_s, capture_evidence,
        config.camera.restart_attempts, config.camera.restart_backoff_s,
    ).run(phase_duration, "capture_only")
    if capture.status != "PASS" or capture.frames_captured < 100:
        print("CAPTURE_SHORT_PASS failed; detector benchmark was not started", file=sys.stderr)
        (ROOT / "STAGE_4_REPORT.md").write_text(render(capture_writer.run_directory), encoding="utf-8")
        return 1

    detector = create_detector(config, args.backend, args.model)
    samples = capture_writer.run_directory / "samples"
    inference_writer = VisionRunWriter(config.runtime.run_root)
    inference_writer.json("camera_modes.json", modes)
    BenchmarkRunner(
        create_camera_source(config, "image_directory", samples, loop=True), detector, inference_writer,
        config.queue.size, config.benchmark.warmup_frames, config.benchmark.resource_sample_interval_s,
        restart_attempts=config.camera.restart_attempts, restart_backoff_s=config.camera.restart_backoff_s,
    ).run(phase_duration, "inference_only")

    end_writer = VisionRunWriter(config.runtime.run_root)
    end_writer.json("camera_modes.json", modes)
    end_evidence = EvidenceRecorder(
        end_writer.run_directory, args.save_samples, config.evidence.confidence_threshold,
        config.evidence.cooldown_s, config.evidence.sample_interval_s, config.evidence.max_images,
    )
    end_summary = BenchmarkRunner(
        create_camera_source(config), create_detector(config, args.backend, args.model), end_writer,
        config.queue.size, config.benchmark.warmup_frames,
        config.benchmark.resource_sample_interval_s, end_evidence,
        config.camera.restart_attempts, config.camera.restart_backoff_s,
    ).run(args.duration, "end_to_end")
    (ROOT / "STAGE_4_REPORT.md").write_text(render(end_writer.run_directory), encoding="utf-8")
    print(f"Capture-only: {capture_writer.run_directory}")
    print(f"Inference-only: {inference_writer.run_directory}")
    print(f"End-to-end: {end_writer.run_directory}")
    print(ROOT / "STAGE_4_REPORT.md")
    return 0 if end_summary.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
