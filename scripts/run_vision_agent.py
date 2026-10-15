#!/usr/bin/env python3
"""Run the independent local vision agent; no flight-control interface is imported."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.agent import VisionAgent
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.evidence import EvidenceRecorder
from rescue_core.vision.factory import create_camera_source, create_detector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "vision.yaml")
    parser.add_argument("--source", choices=("synthetic", "image_directory", "video", "picamera2"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--backend", choices=("mock", "onnx", "ncnn"))
    args = parser.parse_args()
    config = load_vision_config(args.config)
    source = create_camera_source(config, args.source, args.input, loop=True)
    detector = create_detector(config, args.backend, args.model)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    run_directory = config.runtime.run_root / f"{stamp}_agent"
    run_directory.mkdir(parents=True, exist_ok=False)
    events_path = run_directory / "events.ndjson"
    evidence = EvidenceRecorder(
        run_directory, config.evidence.enabled, config.evidence.confidence_threshold,
        config.evidence.cooldown_s, config.evidence.sample_interval_s, config.evidence.max_images,
    )

    def event(event_value) -> None:
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event_value.as_dict(), sort_keys=True, separators=(",", ":")) + "\n")

    agent = VisionAgent(
        source, detector, config.queue.size, config.camera.no_frame_timeout_s,
        evidence, event, config.runtime.event_output,
        config.camera.restart_attempts, config.camera.restart_backoff_s,
    )
    signal.signal(signal.SIGTERM, lambda *_: agent.stop())
    signal.signal(signal.SIGINT, lambda *_: agent.stop())
    try:
        agent.run()
    except Exception as exc:
        print(f"VISION AGENT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
