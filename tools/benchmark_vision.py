#!/usr/bin/env python3
"""Run a reproducible local capture, inference, or end-to-end vision benchmark."""

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


def run(args: argparse.Namespace) -> Path:
    config = load_vision_config(args.config)
    if args.profile:
        config.camera.profile = args.profile
    source = create_camera_source(config, args.source, args.input, loop=args.loop)
    detector = None if args.mode == "capture_only" else create_detector(config, args.backend, args.model)
    writer = VisionRunWriter(args.output_root or config.runtime.run_root)
    evidence = EvidenceRecorder(
        writer.run_directory,
        enabled=args.save_samples or config.evidence.enabled,
        confidence_threshold=config.evidence.confidence_threshold,
        cooldown_s=config.evidence.cooldown_s,
        sample_interval_s=config.evidence.sample_interval_s,
        max_images=config.evidence.max_images,
    )
    runner = BenchmarkRunner(
        source, detector, writer, config.queue.size,
        args.warmup if args.warmup is not None else config.benchmark.warmup_frames,
        config.benchmark.resource_sample_interval_s, evidence,
        config.camera.restart_attempts, config.camera.restart_backoff_s,
    )
    summary = runner.run(args.duration or config.benchmark.duration_s, args.mode)
    print(writer.run_directory)
    if summary.status != "PASS":
        raise RuntimeError("Vision benchmark failed: " + "; ".join(summary.errors))
    return writer.run_directory


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=ROOT / "config" / "vision.yaml")
    result.add_argument("--mode", choices=("capture_only", "inference_only", "end_to_end"), default="end_to_end")
    result.add_argument("--source", choices=("synthetic", "image_directory", "video", "picamera2"))
    result.add_argument("--input", type=Path, help="Image directory or local video path")
    result.add_argument("--profile", help="Configured camera profile")
    result.add_argument("--backend", choices=("mock", "onnx", "ncnn"))
    result.add_argument("--model", help="Model ID from models/manifest.yaml")
    result.add_argument("--duration", type=float)
    result.add_argument("--warmup", type=int)
    result.add_argument("--loop", action="store_true")
    result.add_argument("--save-samples", action="store_true")
    result.add_argument("--output-root", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        run(args)
    except Exception as exc:
        print(f"VISION BENCHMARK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
