"""Final untouched-set acceptance for the selected V3 checkpoint."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from evaluate_stage13sim_nadir_yolo import evaluate


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data" / "training" / "stage13sim_nadir_person_v1"
V2 = ROOT / "data" / "training" / "stage13sim_nadir_person_v2"
V3 = ROOT / "data" / "training" / "stage13sim_nadir_person_v3"
MODELS = {
    "v2": V2 / "training_runs" / "primary" / "weights" / "epoch6.onnx",
    "v3": V3 / "training_runs" / "primary" / "weights" / "epoch11.onnx",
}


def summary(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if key != "frames"}


def mission_sequence(metrics: dict, records: list[dict]) -> dict:
    tracks: dict[int, list[tuple[float, bool, float]]] = defaultdict(list)
    for record, frame in zip(records, metrics["frames"]):
        predictions = frame["passed_predictions"]
        tracks[int(record["track_id"])].append((
            float(record["source_time_s"]), bool(predictions),
            max((float(item["confidence"]) for item in predictions), default=0.0),
        ))
    max_streak = false_confirms = false_frames = 0
    maximum_confidence = 0.0
    for values in tracks.values():
        streak = 0
        confirmed = False
        for _, present, confidence in sorted(values):
            false_frames += int(present)
            maximum_confidence = max(maximum_confidence, confidence)
            streak = streak + 1 if present else 0
            max_streak = max(max_streak, streak)
            confirmed |= streak >= 3
        false_confirms += int(confirmed)
    return {
        "frames": len(records), "tracks": len(tracks),
        "false_detection_frames": false_frames,
        "maximum_false_confidence": maximum_confidence,
        "maximum_false_streak": max_streak,
        "false_three_frame_confirmation_count": false_confirms,
    }


def main() -> None:
    original_manifest = json.loads((V1 / "manifest.json").read_text(encoding="utf-8"))
    original = sorted((row for row in original_manifest if row["split"] == "heldout"),
                      key=lambda row: int(row["source_time_ns"]))
    mission = sorted(json.loads((V3 / "mission_holdout" / "manifest.json").read_text(encoding="utf-8")),
                     key=lambda row: (int(row["track_id"]), float(row["source_time_s"])))
    original_results = {name: evaluate(name, path, original) for name, path in MODELS.items()}
    mission_results = {name: evaluate(name, path, mission) for name, path in MODELS.items()}
    mission_sequences = {name: mission_sequence(mission_results[name], mission) for name in MODELS}
    v3 = original_results["v3"]
    m3 = mission_sequences["v3"]
    accepted = (
        v3["detections_at_threshold"] >= 20
        and v3["recall"] >= 0.50
        and v3["maximum_false_streak"] <= 2
        and not v3["false_three_frame_confirmation"]
        and m3["maximum_false_streak"] <= 2
        and m3["false_three_frame_confirmation_count"] == 0
    )
    output = {
        "accepted": accepted,
        "architecture": "YOLO11n", "input_resolution": 640,
        "confidence_threshold": 0.25, "confirmation_observations": 3,
        "original_heldout": {name: summary(value) for name, value in original_results.items()},
        "mission_negative_holdout": mission_sequences,
        "confirmation_sequence_replay": "frozen three-consecutive-observation semantics",
    }
    (V3 / "V3_FINAL_ACCEPTANCE.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
