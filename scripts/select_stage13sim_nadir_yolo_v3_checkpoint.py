"""Select one checkpoint from the single V3 run using sequence-level safety."""

from __future__ import annotations

import csv
import gc
import json
import re
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "data" / "training" / "stage13sim_nadir_person_v3"
WEIGHTS = V3 / "training_runs" / "primary" / "weights"
THRESHOLD = 0.25


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0.0


def truth_boxes(label: Path) -> list[list[float]]:
    boxes: list[list[float]] = []
    for line in label.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        _, cx, cy, width, height = map(float, line.split())
        boxes.append([(cx-width/2)*1280, (cy-height/2)*800,
                      (cx+width/2)*1280, (cy+height/2)*800])
    return boxes


def evaluate(checkpoint: Path) -> dict:
    model = YOLO(str(checkpoint))
    val_images = sorted((V3 / "images" / "val").glob("*.png"))
    val_results = model.predict([str(p) for p in val_images], imgsz=640, conf=THRESHOLD,
                                classes=[0], device=0, batch=16, verbose=False)
    positive = detected = false_frames = 0
    for image, result in zip(val_images, val_results):
        predictions = result.boxes.xyxy.cpu().tolist() if result.boxes is not None else []
        truth = truth_boxes(V3 / "labels" / "val" / f"{image.stem}.txt")
        matched = bool(truth) and any(max(iou(box, target) for target in truth) >= 0.10 for box in predictions)
        positive += int(bool(truth))
        detected += int(matched)
        false_frames += int(bool(predictions) and not matched)

    holdout_images = sorted((V3 / "mission_holdout" / "images").glob("*.png"))
    holdout_results = model.predict([str(p) for p in holdout_images], imgsz=640, conf=THRESHOLD,
                                    classes=[0], device=0, batch=16, verbose=False)
    by_track: dict[int, list[tuple[int, bool]]] = defaultdict(list)
    holdout_false = 0
    maximum_confidence = 0.0
    pattern = re.compile(r"retry27_track(\d+)_(\d+)_")
    for image, result in zip(holdout_images, holdout_results):
        match = pattern.match(image.name)
        assert match
        boxes = result.boxes
        present = boxes is not None and len(boxes) > 0
        holdout_false += int(present)
        if present:
            maximum_confidence = max(maximum_confidence, float(boxes.conf.max().cpu()))
        by_track[int(match.group(1))].append((int(match.group(2)), present))
    max_streak = false_confirms = 0
    for values in by_track.values():
        streak = 0
        confirmed = False
        for _, present in sorted(values):
            streak = streak + 1 if present else 0
            max_streak = max(max_streak, streak)
            confirmed |= streak >= 3
        false_confirms += int(confirmed)
    del model
    gc.collect()
    return {
        "checkpoint": str(checkpoint.relative_to(ROOT)),
        "validation_positive_frames": positive,
        "validation_positive_detections": detected,
        "validation_positive_recall": detected / positive,
        "validation_false_detection_frames": false_frames,
        "mission_holdout_frames": len(holdout_images),
        "mission_holdout_false_detections": holdout_false,
        "mission_holdout_maximum_confidence": maximum_confidence,
        "mission_holdout_maximum_false_streak": max_streak,
        "mission_holdout_false_three_frame_confirmations": false_confirms,
    }


def main() -> None:
    checkpoints = sorted(WEIGHTS.glob("epoch*.pt"), key=lambda p: int(p.stem.removeprefix("epoch")))
    evaluations = [evaluate(path) for path in checkpoints]
    with (V3 / "training_runs" / "primary" / "results.csv").open(newline="", encoding="utf-8") as stream:
        metrics = [{key.strip(): value for key, value in row.items()} for row in csv.DictReader(stream)]
    for row in evaluations:
        epoch = int(Path(row["checkpoint"]).stem.removeprefix("epoch"))
        metric = metrics[epoch]
        row.update({
            "precision": float(metric["metrics/precision(B)"]),
            "recall": float(metric["metrics/recall(B)"]),
            "mAP50": float(metric["metrics/mAP50(B)"]),
            "mAP50-95": float(metric["metrics/mAP50-95(B)"]),
        })
    safe = [row for row in evaluations
            if row["mission_holdout_false_three_frame_confirmations"] == 0
            and row["mission_holdout_maximum_false_streak"] <= 2]
    selected = max(safe, key=lambda row: (
        row["validation_positive_recall"],
        -row["mission_holdout_false_detections"],
        -row["validation_false_detection_frames"],
        row["mAP50-95"],
    )) if safe else None
    report = {"threshold": THRESHOLD, "evaluations": evaluations, "selected": selected}
    (V3 / "V3_CHECKPOINT_SELECTION.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"selected": selected, "safe_checkpoints": len(safe)}, indent=2))


if __name__ == "__main__":
    main()
