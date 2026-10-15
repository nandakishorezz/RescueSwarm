"""Compare the original and domain-adapted YOLO11n on exact held-out pixels."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from rescue_core.vision.detectors import decode_yolo_output
from rescue_core.vision.models import FramePacket
from rescue_core.vision.preprocess import VisionPreprocessor, non_maximum_suppression


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "training" / "stage13sim_nadir_person_v1"
V2_DATASET = ROOT / "data" / "training" / "stage13sim_nadir_person_v2"
MODELS = {
    "original": ROOT / "models" / "artifacts" / "yolo11n.onnx",
    "v1": DATASET / "training_runs" / "primary" / "weights" / "best.onnx",
    "v2": V2_DATASET / "training_runs" / "primary" / "weights" / "epoch6.onnx",
}
THRESHOLD = 0.25


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def evaluate(name: str, path: Path, records: list[dict]) -> dict:
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    preprocessor = VisionPreprocessor(640, 640, "zero_to_one")
    start = time.perf_counter()
    frame_results = []
    true_confidences: list[float] = []
    false_confidences: list[float] = []
    positive_detected = 0
    false_frames = 0
    max_false_streak = false_streak = 0
    detection_times = []
    processed = 0
    for sequence, record in enumerate(records):
        processed += 1
        image = np.asarray(Image.open(ROOT / record["image"]).convert("RGB"), dtype=np.uint8)
        packet = FramePacket(
            record.get("frame_id", f"heldout:{sequence}"), "heldout", time.monotonic_ns(), time.time_ns(),
            sequence, image.shape[1], image.shape[0], "RGB8", image,
            capture_timestamp_sim_ns=int(record.get("source_time_ns", 0)),
        )
        prepared = preprocessor.prepare(packet)[0]
        output = session.run([output_name], {input_name: prepared.tensor})[0]
        raw = decode_yolo_output(np.asarray(output))
        restored = [
            preprocessor.restore(item, prepared, packet, "person")
            for item in raw if item.class_id == 0 and item.confidence >= THRESHOLD
        ]
        detections = non_maximum_suppression(restored, 0.45)
        passed = [([d.x1, d.y1, d.x2, d.y2], float(d.confidence)) for d in detections]
        scores = np.asarray([item.confidence for item in raw if item.class_id == 0], dtype=np.float32)
        truth = record.get("boxes", [])
        matched = False
        matched_score = 0.0
        if truth:
            for box, score in passed:
                if max(iou(box, target) for target in truth) >= 0.10:
                    matched = True
                    matched_score = max(matched_score, score)
            if matched:
                positive_detected += 1
                true_confidences.append(matched_score)
                detection_times.append(int(record["source_time_ns"]))
        false = bool(passed) and not matched
        if false:
            false_frames += 1
            false_confidences.extend(score for _, score in passed)
            false_streak += 1
            max_false_streak = max(max_false_streak, false_streak)
        else:
            false_streak = 0
        frame_results.append({
            "frame_id": record.get("frame_id"),
            "source_time_ns": record.get("source_time_ns"),
            "truth_visible": bool(truth),
            "matched_detection": matched,
            "passed_predictions": [{"box": box, "confidence": score} for box, score in passed],
            "maximum_person_score": float(scores.max()) if scores.size else 0.0,
        })
    elapsed = time.perf_counter() - start
    gaps = [
        (later - earlier) / 1e9
        for earlier, later in zip(detection_times, detection_times[1:])
    ]
    positives = sum(bool(record.get("boxes")) for record in records)
    return {
        "model": name,
        "checkpoint": str(path.relative_to(ROOT)),
        "architecture": "YOLO11n",
        "input_resolution": 640,
        "confidence_threshold": THRESHOLD,
        "heldout_frames": processed,
        "visible_frames": positives,
        "negative_frames": len(records) - positives,
        "detections_at_threshold": positive_detected,
        "recall": positive_detected / positives if positives else 0.0,
        "false_detection_frames": false_frames,
        "false_three_frame_confirmation": max_false_streak >= 3,
        "maximum_false_streak": max_false_streak,
        "median_true_confidence": statistics.median(true_confidences) if true_confidences else 0.0,
        "maximum_true_confidence": max(true_confidences, default=0.0),
        "maximum_false_confidence": max(false_confidences, default=0.0),
        "median_detection_gap_s": statistics.median(gaps) if gaps else None,
        "maximum_detection_gap_s": max(gaps, default=None),
        "elapsed_s": elapsed,
        "fps": processed / elapsed,
        "frames": frame_results,
    }


def main() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    heldout = sorted(
        (record for record in manifest if record["split"] == "heldout"),
        key=lambda record: int(record["source_time_ns"]),
    )
    report = {name: evaluate(name, path, heldout) for name, path in MODELS.items()}
    summary = {
        name: {key: value for key, value in metrics.items() if key != "frames"}
        for name, metrics in report.items()
    }
    accepted = (
        report["v2"]["detections_at_threshold"] >= 20
        and report["v2"]["recall"] >= 0.50
        and report["v2"]["maximum_false_streak"] <= 2
        and not report["v2"]["false_three_frame_confirmation"]
        and report["v2"]["false_detection_frames"] <= report["v1"]["false_detection_frames"]
    )
    output = {"accepted": accepted, "summary": summary, "details": report}
    (V2_DATASET / "V2_FINAL_HELDOUT_COMPARISON.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"accepted": accepted, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
