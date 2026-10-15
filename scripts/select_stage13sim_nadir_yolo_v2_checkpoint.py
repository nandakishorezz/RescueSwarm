"""Select one checkpoint from the single V2 run using operational validation safety."""

from __future__ import annotations

import gc
import csv
import json
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "data" / "training" / "stage13sim_nadir_person_v2"
WEIGHTS = V2 / "training_runs" / "primary" / "weights"
THRESHOLD = 0.25


def iou(a: list[float], b: list[float]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0.0


def labels_for(record: dict) -> list[list[float]]:
    image = ROOT / record["image"]
    label = V2 / "labels" / "val" / f"{image.stem}.txt"
    truth = []
    for line in label.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        _, cx, cy, width, height = map(float, line.split())
        truth.append([
            (cx - width / 2) * 1280, (cy - height / 2) * 800,
            (cx + width / 2) * 1280, (cy + height / 2) * 800,
        ])
    return truth


def evaluate(checkpoint: Path, records: list[dict]) -> dict:
    model = YOLO(str(checkpoint))
    positive, positive_detected, false_frames = 0, 0, 0
    group_flags: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for index, record in enumerate(records):
        result = model.predict(str(ROOT / record["image"]), imgsz=640, conf=THRESHOLD,
                               classes=[0], device=0, verbose=False)[0]
        predictions = result.boxes.xyxy.cpu().tolist() if result.boxes is not None else []
        truth = labels_for(record)
        matched = bool(truth) and any(max(iou(box, target) for target in truth) >= 0.10 for box in predictions)
        if truth:
            positive += 1
            positive_detected += int(matched)
        false = bool(predictions) and not matched
        false_frames += int(false)
        order = float(record.get("source_time_s", index))
        group_flags[record.get("source_group", "unknown")].append((order, false))
    max_streak = 0
    for values in group_flags.values():
        streak = 0
        for _, false in sorted(values):
            streak = streak + 1 if false else 0
            max_streak = max(max_streak, streak)
    del model
    gc.collect()
    return {
        "checkpoint": str(checkpoint.relative_to(ROOT)),
        "positive_frames": positive,
        "positive_detections": positive_detected,
        "positive_recall": positive_detected / positive,
        "negative_false_detection_frames": false_frames,
        "maximum_negative_false_streak": max_streak,
        "false_three_frame_confirmations": max_streak >= 3,
    }


def main() -> None:
    records = [row for row in json.loads((V2 / "manifest.json").read_text()) if row["split"] == "val"]
    checkpoints = sorted(WEIGHTS.glob("epoch*.pt"), key=lambda p: int(p.stem.removeprefix("epoch")))
    report_path = V2 / "V2_CHECKPOINT_SELECTION.json"
    if report_path.exists():
        evaluations = json.loads(report_path.read_text(encoding="utf-8"))["evaluations"]
    else:
        evaluations = [evaluate(checkpoint, records) for checkpoint in checkpoints]
    with (V2 / "training_runs" / "primary" / "results.csv").open(newline="", encoding="utf-8") as stream:
        metrics = [{key.strip(): value for key, value in row.items()} for row in csv.DictReader(stream)]
    for row in evaluations:
        epoch_index = int(Path(row["checkpoint"]).stem.removeprefix("epoch"))
        metric = metrics[epoch_index]
        row["precision"] = float(metric["metrics/precision(B)"])
        row["recall"] = float(metric["metrics/recall(B)"])
        row["mAP50"] = float(metric["metrics/mAP50(B)"])
        row["mAP50-95"] = float(metric["metrics/mAP50-95(B)"])
    safe = [row for row in evaluations if not row["false_three_frame_confirmations"]]
    candidates = safe or evaluations
    selected = max(candidates, key=lambda row: (
        row["positive_recall"], -row["negative_false_detection_frames"],
        -row["maximum_negative_false_streak"], row["mAP50-95"],
    ))
    report = {"threshold": THRESHOLD, "evaluations": evaluations, "selected": selected}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
