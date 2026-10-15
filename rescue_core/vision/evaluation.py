"""Labelled YOLO-box evaluation; never invents accuracy when labels are absent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Detection
from .preprocess import intersection_over_union


@dataclass(slots=True, frozen=True)
class EvaluationMetrics:
    threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def load_yolo_labels(path: str | Path, width: int, height: int, class_names: list[str]) -> list[Detection]:
    labels: list[Detection] = []
    label_path = Path(path)
    if not label_path.is_file():
        return labels
    for number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        values = line.split()
        if len(values) != 5:
            raise ValueError(f"Invalid YOLO label at {label_path}:{number}")
        class_id = int(values[0])
        center_x, center_y, box_width, box_height = map(float, values[1:])
        if class_id < 0 or class_id >= len(class_names):
            raise ValueError(f"Unknown class ID at {label_path}:{number}")
        labels.append(Detection(
            class_id, class_names[class_id], 1,
            (center_x - box_width / 2) * width, (center_y - box_height / 2) * height,
            (center_x + box_width / 2) * width, (center_y + box_height / 2) * height,
            width, height,
        ))
    return labels


def evaluate_boxes(
    predictions: dict[str, list[Detection]], ground_truth: dict[str, list[Detection]],
    thresholds: list[float], iou_threshold: float = 0.5,
) -> list[EvaluationMetrics]:
    if not ground_truth:
        return []
    metrics: list[EvaluationMetrics] = []
    image_ids = sorted(set(predictions) | set(ground_truth))
    for threshold in thresholds:
        tp = fp = fn = 0
        for image_id in image_ids:
            candidates = sorted(
                (item for item in predictions.get(image_id, []) if item.confidence >= threshold),
                key=lambda item: -item.confidence,
            )
            expected = ground_truth.get(image_id, [])
            matched: set[int] = set()
            for candidate in candidates:
                options = [
                    (intersection_over_union(candidate, truth), index)
                    for index, truth in enumerate(expected)
                    if index not in matched and candidate.class_id == truth.class_id
                ]
                best = max(options, default=(0, -1))
                if best[0] >= iou_threshold:
                    tp += 1
                    matched.add(best[1])
                else:
                    fp += 1
            fn += len(expected) - len(matched)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        metrics.append(EvaluationMetrics(threshold, tp, fp, fn, precision, recall, f1))
    return metrics


def average_precision(metrics: list[EvaluationMetrics]) -> float | None:
    if not metrics:
        return None
    points = sorted((item.recall, item.precision) for item in metrics)
    area = 0.0
    previous_recall = 0.0
    for recall, precision in points:
        area += max(0, recall - previous_recall) * precision
        previous_recall = max(previous_recall, recall)
    return area
