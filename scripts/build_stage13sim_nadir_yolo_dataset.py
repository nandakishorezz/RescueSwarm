"""Build a leakage-safe YOLO dataset from Stage13SIM rendered pixels."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scratch" / "camera_route_coddesign_sweep"
HELDOUT_RUN = ROOT / "data" / "simulation_runs" / "stage13sim_exact_pixel_capture_retry26"
OUT = ROOT / "data" / "training" / "stage13sim_nadir_person_v1"
VALIDATION_GROUPS = {
    "results_prequal_search_smoke.json",
    "results_robustsearch_0_r7.json",
    "results_route_0_reacquisition.json",
    "results_retry26_closest.json",
}


def yolo_line(box: tuple[int, int, int, int], width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    return (
        f"0 {(x1 + x2) / (2 * width):.8f} {(y1 + y2) / (2 * height):.8f} "
        f"{(x2 - x1) / width:.8f} {(y2 - y1) / height:.8f}\n"
    )


def rendered_truth_box(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """Recover the actor extent from the uniform Stage13SIM calibration ground."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    values, counts = np.unique(gray, return_counts=True)
    background = int(values[int(np.argmax(counts))])
    mask = np.abs(gray.astype(np.int16) - background) > 2
    ys, xs = np.where(mask)
    if xs.size < 25:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def reset_output() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for split in ("train", "val", "heldout"):
        (OUT / "images" / split).mkdir(parents=True)
        (OUT / "labels" / split).mkdir(parents=True)


def add_static() -> tuple[list[dict], Counter]:
    manifest: list[dict] = []
    counts: Counter = Counter()
    seen_hashes: set[str] = set()
    for result_file in sorted(SOURCE.glob("results*.json")):
        try:
            records = json.loads(result_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(records, list):
            continue
        split = "val" if result_file.name in VALIDATION_GROUPS else "train"
        for record in records:
            try:
                pitch = float(record.get("pitch_deg", 999.0))
            except (TypeError, ValueError):
                continue
            if pitch > 10.0 or record.get("category") not in {
                "search", "approach", "reacquisition", "delivery", "forensic", "basis"
            }:
                continue
            source_path = ROOT / str(record.get("frame", "")).replace("\\", "/")
            if not source_path.is_file():
                continue
            raw = source_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen_hashes:
                counts["duplicates_removed"] += 1
                continue
            seen_hashes.add(digest)
            image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            height, width = image.shape[:2]
            box = rendered_truth_box(image)
            name = f"{result_file.stem}_{source_path.stem}_{digest[:10]}.png"
            destination = OUT / "images" / split / name
            shutil.copy2(source_path, destination)
            label = OUT / "labels" / split / f"{destination.stem}.txt"
            label.write_text(yolo_line(box, width, height) if box else "", encoding="utf-8")
            kind = "positive" if box else "negative"
            counts[f"{split}_{kind}"] += 1
            manifest.append({
                "split": split,
                "source_group": result_file.name,
                "source": str(source_path.relative_to(ROOT)),
                "image": str(destination.relative_to(ROOT)),
                "sha256": digest,
                "pitch_deg": pitch,
                "category": record.get("category"),
                "box": list(box) if box else None,
            })
    return manifest, counts


def add_exact_run_intervals(manifest: list[dict], counts: Counter) -> None:
    audit = HELDOUT_RUN / "exact_inference_audit"
    frame_index = {
        row["frame_id"]: row
        for line in (audit / "exact_frame_index.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))
    }
    evaluation = json.loads((HELDOUT_RUN / "perception_evaluation.json").read_text(encoding="utf-8"))
    positive_times = [
        int(frame["sim_capture_time_ns"])
        for frame in evaluation["frames"]
        if frame["truth_boxes"]
    ]
    train_negative_cutoff = min(positive_times) - 2_000_000_000
    for frame in evaluation["frames"]:
        index = frame_index.get(frame["frame_id"])
        if index is None:
            continue
        source_path = audit / "exact_rgb_frames" / index["exact_rgb_file"]
        if not source_path.is_file():
            continue
        split = "heldout"
        if not frame["truth_boxes"] and int(frame["sim_capture_time_ns"]) < train_negative_cutoff:
            split = "train"
        destination = OUT / "images" / split / source_path.name
        shutil.copy2(source_path, destination)
        labels = []
        boxes = []
        for truth in frame["truth_boxes"]:
            box = (int(truth["x1"]), int(truth["y1"]), int(truth["x2"]), int(truth["y2"]))
            labels.append(yolo_line(box, int(truth["frame_width"]), int(truth["frame_height"])))
            boxes.append(list(box))
        (OUT / "labels" / split / f"{source_path.stem}.txt").write_text(
            "".join(labels), encoding="utf-8"
        )
        kind = "positive" if boxes else "negative"
        counts[f"{split}_{kind}"] += 1
        manifest.append({
            "split": split,
            "source_group": HELDOUT_RUN.name,
            "source": str(source_path.relative_to(ROOT)),
            "image": str(destination.relative_to(ROOT)),
            "sha256": index["raw_checksum_sha256"],
            "pitch_deg": 0.0,
            "category": "live_search",
            "frame_id": frame["frame_id"],
            "source_time_ns": frame["sim_capture_time_ns"],
            "boxes": boxes,
        })


def main() -> None:
    reset_output()
    manifest, counts = add_static()
    add_exact_run_intervals(manifest, counts)
    yaml = (
        f"path: {OUT.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n  0: person\n"
    )
    (OUT / "dataset.yaml").write_text(yaml, encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = {
        "dataset": "stage13sim_nadir_person_v1",
        "label_source": {
            "static": "verified rendered actor pixels against uniform Stage13SIM calibration ground",
            "heldout": "SIMULATION_EVALUATOR_ONLY semantic truth boxes",
        },
        "split_policy": {
            "train": "static render pose groups plus exact-run pre-visibility negative interval",
            "validation": sorted(VALIDATION_GROUPS),
            "heldout": [f"{HELDOUT_RUN.name}: source time >= first visibility minus 2 seconds"],
            "adjacent_frame_leakage": False,
        },
        "counts": dict(counts),
        "total": len(manifest),
    }
    (OUT / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
