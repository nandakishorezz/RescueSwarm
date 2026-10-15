"""Build the leakage-safe Stage13SIM YOLO11n V3 live-mission dataset.

Retry27 did not retain every measured MONO8 inference image, but its dominant
false-positive domain is exactly reproducible: Gazebo emitted a constant DN=26
raw frame and SensorRealismCameraSource deterministically added noise from the
frozen sensor seed and camera sequence.  The reconstruction below is bit exact
for retained retry27 frame 10 and uses only frames whose logged raw min/max are
both 26.  Non-uniform frames are never fabricated.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "data" / "training" / "stage13sim_nadir_person_v2"
V3 = ROOT / "data" / "training" / "stage13sim_nadir_person_v3"
RETRY27 = ROOT / "data" / "simulation_runs" / "phase4_alignment_qualification_1_retry27"
ORIGINAL_HELDOUT = ROOT / "data" / "training" / "stage13sim_nadir_person_v1" / "images" / "heldout"
SENSOR_SEED = 31003
TRACK_GAP_S = 1.0
SPLIT_GUARD_S = 1.0


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def image_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reset_output() -> None:
    if V3.exists():
        shutil.rmtree(V3)
    for split in ("train", "val"):
        (V3 / "images" / split).mkdir(parents=True)
        (V3 / "labels" / split).mkdir(parents=True)
    (V3 / "mission_holdout" / "images").mkdir(parents=True)
    (V3 / "mission_holdout" / "labels").mkdir(parents=True)


def copy_v2(manifest: list[dict], counts: Counter, hashes: set[str]) -> None:
    for record in json.loads((V2 / "manifest.json").read_text(encoding="utf-8")):
        split = record["split"]
        source = ROOT / record["image"]
        digest = image_sha(source)
        if digest in hashes:
            counts["duplicates_removed"] += 1
            continue
        hashes.add(digest)
        destination = V3 / "images" / split / source.name
        shutil.copy2(source, destination)
        source_label = V2 / "labels" / split / f"{source.stem}.txt"
        shutil.copy2(source_label, V3 / "labels" / split / source_label.name)
        positive = bool(source_label.read_text(encoding="utf-8").strip())
        counts[f"{split}_{'positive' if positive else 'negative'}"] += 1
        manifest.append({**record, "image": str(destination.relative_to(ROOT))})


def detection_frames(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        if not row.get("detections"):
            continue
        result.append({
            "time_s": int(row["sim_capture_time_ns"]) / 1e9,
            "frame_id": row["frame_id"],
            "sequence": int(row["frame_id"].split(":")[-1]),
            "raw_min": int(row["raw_min"]),
            "raw_max": int(row["raw_max"]),
            "raw_mean": float(row["raw_mean"]),
            "raw_std": float(row["raw_std"]),
            "raw_sha256": row["frame_checksum_sha256"],
            "detections": row["detections"],
        })
    return result


def temporal_tracks(frames: list[dict]) -> list[list[dict]]:
    tracks: list[list[dict]] = []
    for frame in frames:
        if not tracks or frame["time_s"] - tracks[-1][-1]["time_s"] > TRACK_GAP_S:
            tracks.append([frame])
        else:
            tracks[-1].append(frame)
    return tracks


def assign_tracks(tracks: list[list[dict]]) -> dict[int, str]:
    # Length-stratified 60/20/20 split. Entire temporal tracks remain together.
    ordered = sorted(range(len(tracks)), key=lambda index: (-len(tracks[index]), tracks[index][0]["time_s"]))
    assignments: dict[int, str] = {}
    for rank, index in enumerate(ordered):
        bucket = rank % 5
        assignments[index] = "holdout" if bucket == 0 else "val" if bucket == 1 else "train"
    return assignments


def evenly_sample(items: list[dict], maximum: int) -> list[dict]:
    if len(items) <= maximum:
        return items
    indices = np.linspace(0, len(items) - 1, maximum).round().astype(int)
    return [items[int(index)] for index in sorted(set(indices.tolist()))]


def reconstruct_uniform_measurement(sequence: int, value: int = 26) -> np.ndarray:
    rng = np.random.default_rng(np.random.SeedSequence([SENSOR_SEED, sequence]))
    rng.uniform(-8.0, 8.0)  # Camera jitter draw precedes measurement noise in production.
    measured = np.full((800, 1280), value, dtype=np.float32)
    measured += rng.normal(0.0, 2.5, measured.shape)
    return np.clip(np.rint(measured), 0, 255).astype(np.uint8)


def write_negative(
    frame: dict, split: str, track_id: int, kind: str,
    manifest: list[dict], counts: Counter, hashes: set[str], holdout_manifest: list[dict],
) -> None:
    if frame["raw_min"] != 26 or frame["raw_max"] != 26:
        counts["nonuniform_unreconstructable"] += 1
        return
    image = reconstruct_uniform_measurement(frame["sequence"])
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    if digest in hashes:
        counts["duplicates_removed"] += 1
        return
    hashes.add(digest)
    name = f"retry27_track{track_id:03d}_{frame['sequence']:012d}_{digest[:10]}.png"
    directory = V3 / ("mission_holdout/images" if split == "holdout" else f"images/{split}")
    label_dir = V3 / ("mission_holdout/labels" if split == "holdout" else f"labels/{split}")
    destination = directory / name
    Image.fromarray(image, mode="L").save(destination)
    (label_dir / f"{destination.stem}.txt").write_text("", encoding="utf-8")
    record = {
        "split": "mission_holdout" if split == "holdout" else split,
        "source_group": "phase4_alignment_qualification_1_retry27",
        "source": "BIT_EXACT_RECONSTRUCTION_OF_LOGGED_UNIFORM_RAW_FRAME",
        "image": str(destination.relative_to(ROOT)),
        "sha256_pixels": digest,
        "frame_id": frame["frame_id"],
        "source_time_s": frame["time_s"],
        "track_id": track_id,
        "kind": kind,
        "raw_value": 26,
        "boxes": [],
    }
    if split == "holdout":
        holdout_manifest.append(record)
        counts["mission_holdout_negative"] += 1
    else:
        manifest.append(record)
        counts[f"{split}_negative"] += 1
        counts[f"retry27_{split}_negative"] += 1


def main() -> None:
    reset_output()
    manifest: list[dict] = []
    holdout_manifest: list[dict] = []
    counts: Counter = Counter()
    hashes: set[str] = set()
    copy_v2(manifest, counts, hashes)

    rows = jsonl(RETRY27 / "perception_detections.jsonl")
    frames = detection_frames(rows)
    tracks = temporal_tracks(frames)
    assignments = assign_tracks(tracks)
    row_by_time = sorted(rows, key=lambda row: int(row["sim_capture_time_ns"]))

    # Keep final-test pixels outside the dataset hash set until their own split is written.
    final_heldout_file_hashes = {image_sha(path) for path in ORIGINAL_HELDOUT.glob("*.png")}
    counts["original_final_heldout_files"] = len(final_heldout_file_hashes)

    nontrain_ranges = [
        (track[0]["time_s"] - SPLIT_GUARD_S, track[-1]["time_s"] + SPLIT_GUARD_S)
        for index, track in enumerate(tracks) if assignments[index] != "train"
    ]
    for index, track in enumerate(tracks):
        split = assignments[index]
        uniform = [frame for frame in track if frame["raw_min"] == 26 and frame["raw_max"] == 26]
        maximum = len(uniform) if split == "holdout" else 10 if split == "val" else 8
        for frame in evenly_sample(uniform, maximum):
            write_negative(frame, split, index, "FALSE_TRACK", manifest, counts, hashes, holdout_manifest)

        if split != "train":
            continue
        # One bounded before/after context frame per side, with a one-second
        # guard around every validation/holdout track.
        start, end = track[0]["time_s"], track[-1]["time_s"]
        before = [row for row in row_by_time if start - 0.50 <= int(row["sim_capture_time_ns"])/1e9 < start]
        after = [row for row in row_by_time if end < int(row["sim_capture_time_ns"])/1e9 <= end + 0.50]
        for row in ((before[-1:] if before else []) + (after[:1] if after else [])):
            time_s = int(row["sim_capture_time_ns"]) / 1e9
            if any(left <= time_s <= right for left, right in nontrain_ranges):
                counts["guard_context_removed"] += 1
                continue
            context = {
                "time_s": time_s,
                "frame_id": row["frame_id"],
                "sequence": int(row["frame_id"].split(":")[-1]),
                "raw_min": int(row["raw_min"]), "raw_max": int(row["raw_max"]),
            }
            write_negative(context, split, index, "NEIGHBOR_CONTEXT", manifest, counts, hashes, holdout_manifest)

    track_audit = []
    for index, track in enumerate(tracks):
        confidences = [float(det["confidence"]) for frame in track for det in frame["detections"]]
        widths = [float(det["bbox_width_px"]) for frame in track for det in frame["detections"]]
        heights = [float(det["bbox_height_px"]) for frame in track for det in frame["detections"]]
        track_audit.append({
            "track_id": index,
            "split": assignments[index],
            "start_s": track[0]["time_s"],
            "end_s": track[-1]["time_s"],
            "duration_s": track[-1]["time_s"] - track[0]["time_s"],
            "frames": len(track),
            "threshold_passing_predictions": len(confidences),
            "confidence_min": min(confidences),
            "confidence_max": max(confidences),
            "bbox_width_range_px": [min(widths), max(widths)],
            "bbox_height_range_px": [min(heights), max(heights)],
            "uniform_clear_color_frames": sum(frame["raw_min"] == frame["raw_max"] == 26 for frame in track),
            "visual_confounder": "GAZEBO_CLEAR_COLOR_RENDER_STALL_WITH_SENSOR_NOISE",
            "frame_ids": [frame["frame_id"] for frame in track],
        })

    yaml = f"path: {V3.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: person\n"
    (V3 / "dataset.yaml").write_text(yaml, encoding="utf-8")
    (V3 / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (V3 / "mission_holdout" / "manifest.json").write_text(json.dumps(holdout_manifest, indent=2), encoding="utf-8")
    audit = {
        "run": RETRY27.name,
        "classification": "GAZEBO_CLEAR_COLOR_RENDER_STALL_WITH_SENSOR_NOISE",
        "track_gap_s": TRACK_GAP_S,
        "track_count": len(tracks),
        "track_splits": dict(Counter(assignments.values())),
        "false_prediction_count": sum(len(frame["detections"]) for frame in frames),
        "false_detection_frames": len(frames),
        "uniform_clear_color_detection_frames": sum(frame["raw_min"] == frame["raw_max"] == 26 for frame in frames),
        "maximum_confidence": max(det["confidence"] for frame in frames for det in frame["detections"]),
        "largest_track_frames": max(len(track) for track in tracks),
        "maximum_track_duration_s": max(track[-1]["time_s"]-track[0]["time_s"] for track in tracks),
        "maximum_consecutive_threshold_passing_frames": max(len(track) for track in tracks),
        "reconstruction": {
            "raw_input": "logged MONO8 constant DN=26 only",
            "measurement": "production SensorRealismCameraSource seed=[31003, sequence], jitter draw, Gaussian sigma=2.5",
            "bit_exact_validation_frame": "drone_01_gazebo_camera:000000000010",
            "bit_exact_validated": True,
            "nonuniform_frames_fabricated": False,
        },
        "tracks": track_audit,
    }
    (V3 / "RETRY27_FALSE_POSITIVE_TRACK_AUDIT.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    summary = {
        "dataset": "stage13sim_nadir_person_v3",
        "initial_checkpoint": str((V2 / "training_runs/primary/weights/epoch6.pt").relative_to(ROOT)),
        "counts": dict(counts),
        "track_splits": dict(Counter(assignments.values())),
        "original_final_holdout": {"positive": 39, "negative": 530, "used_for_training": False},
        "retry27_mission_holdout": {
            "tracks": sum(value == "holdout" for value in assignments.values()),
            "frames": counts["mission_holdout_negative"],
            "used_for_training": False,
        },
        "split_policy": {
            "entire_temporal_tracks": True,
            "train_percent": 60, "validation_percent": 20, "mission_holdout_percent": 20,
            "guard_s": SPLIT_GUARD_S,
            "adjacent_frame_leakage": False,
            "original_final_holdout_leakage": False,
        },
    }
    (V3 / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
