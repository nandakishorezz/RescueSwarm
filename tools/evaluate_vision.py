#!/usr/bin/env python3
"""Evaluate recorded original-pixel detections against real YOLO labels when available."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.evaluation import average_precision, evaluate_boxes, load_yolo_labels
from rescue_core.vision.models import Detection


def evaluate(run: Path, dataset: Path, thresholds: list[float]) -> dict[str, object]:
    labels_dir, images_dir = dataset / "labels", dataset / "images"
    label_files = sorted(labels_dir.glob("*.txt")) if labels_dir.is_dir() else []
    if not label_files:
        return {"status": "NOT_MEASURED", "reason": "ACCURACY NOT EVALUATED — LABELLED DATASET REQUIRED"}
    frame_names: dict[str, str] = {}
    for line in (run / "frame_stats.ndjson").read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        replay = payload.get("camera_metadata", {}).get("extra", {}).get("replay_path")
        if replay:
            frame_names[payload["frame_id"]] = Path(replay).stem
    predictions: dict[str, list[Detection]] = {}
    for line in (run / "detections.ndjson").read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        image_id = frame_names.get(payload["frame_id"], payload["frame_id"])
        for item in payload["detections"]:
            x1, y1, x2, y2 = item["bbox_pixels"]
            width, height = item["frame_size_pixels"]
            predictions.setdefault(image_id, []).append(Detection(
                item["class_id"], item["class_name"], item["confidence"], x1, y1, x2, y2, width, height
            ))
    ground_truth: dict[str, list[Detection]] = {}
    for label in label_files:
        images = [path for path in images_dir.glob(label.stem + ".*") if path.is_file()]
        if len(images) != 1:
            raise ValueError(f"Expected exactly one image for label {label.name}")
        with Image.open(images[0]) as image:
            ground_truth[label.stem] = load_yolo_labels(label, image.width, image.height, ["person"])
    metrics = evaluate_boxes(predictions, ground_truth, thresholds)
    return {
        "status": "MEASURED", "dataset": str(dataset),
        "threshold_sweep": [asdict(item) for item in metrics],
        "average_precision_approximation": average_precision(metrics),
        "primary_metric": "HUMAN_RECALL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--thresholds", default="0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    thresholds = [float(value) for value in args.thresholds.split(",")]
    result = evaluate(args.run, args.dataset, thresholds)
    output = args.output or args.run / "accuracy_evaluation.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
