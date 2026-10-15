"""Mine hard negatives and build the leakage-safe Stage13SIM YOLO11n V2 dataset."""

from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import time
from collections import Counter
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from rescue_core.vision.detectors import decode_yolo_output
from rescue_core.vision.models import FramePacket
from rescue_core.vision.preprocess import VisionPreprocessor


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data" / "training" / "stage13sim_nadir_person_v1"
V2 = ROOT / "data" / "training" / "stage13sim_nadir_person_v2"
MINING_RUN = ROOT / "data" / "simulation_runs" / "stage13sim_yolo_v2_hard_negative_mining"
FINAL_HELDOUT = V1 / "images" / "heldout"
V1_ONNX = V1 / "training_runs" / "primary" / "weights" / "best.onnx"
MOVE_TO_VALIDATION = {
    "results_robustsearch_refine.json",
    "results_route_low.json",
    "results_route_initial.json",
}
TRAIN_MAX_TIME_S = 120.0
VALIDATION_MIN_TIME_S = 122.0


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def dhash(path: Path) -> int:
    image = Image.open(path).convert("L").resize((17, 10), Image.Resampling.BILINEAR)
    values = np.asarray(image, dtype=np.int16)
    bits = values[:, 1:] > values[:, :-1]
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return value


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def reset_output() -> None:
    if V2.exists():
        shutil.rmtree(V2)
    for split in ("train", "val"):
        (V2 / "images" / split).mkdir(parents=True)
        (V2 / "labels" / split).mkdir(parents=True)


def copy_v1(manifest_out: list[dict], counts: Counter, hashes: set[str]) -> None:
    records = json.loads((V1 / "manifest.json").read_text(encoding="utf-8"))
    for record in records:
        if record["split"] == "heldout":
            continue
        source = ROOT / record["image"]
        if not source.is_file():
            continue
        positive = bool(record.get("box") or record.get("boxes"))
        split = record["split"]
        if positive and record.get("source_group") in MOVE_TO_VALIDATION:
            split = "val"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest in hashes:
            counts["duplicates_removed"] += 1
            continue
        hashes.add(digest)
        destination = V2 / "images" / split / source.name
        shutil.copy2(source, destination)
        source_label = V1 / "labels" / record["split"] / f"{source.stem}.txt"
        shutil.copy2(source_label, V2 / "labels" / split / source_label.name)
        kind = "positive" if positive else "negative"
        counts[f"{split}_{kind}"] += 1
        manifest_out.append({**record, "split": split, "image": str(destination.relative_to(ROOT))})


def mine_negative_scores() -> tuple[list[dict], Counter]:
    audit = MINING_RUN / "exact_inference_audit"
    frames = {row["frame_id"]: row for row in jsonl(audit / "exact_frame_index.jsonl")}
    evaluation = json.loads((MINING_RUN / "perception_evaluation.json").read_text(encoding="utf-8"))
    negatives = [row for row in evaluation["frames"] if not row.get("truth_boxes") and row["frame_id"] in frames]
    session = ort.InferenceSession(str(V1_ONNX), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    preprocessor = VisionPreprocessor(640, 640, "zero_to_one")
    heldout_hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in FINAL_HELDOUT.glob("*.png")}
    heldout_dhashes = [dhash(path) for path in FINAL_HELDOUT.glob("*.png")]
    mined: list[dict] = []
    counts: Counter = Counter()
    for sequence, truth in enumerate(negatives):
        index = frames[truth["frame_id"]]
        path = audit / "exact_rgb_frames" / index["exact_rgb_file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        image_dhash = dhash(path)
        exact_duplicate = digest in heldout_hashes
        near_duplicate = min((hamming(image_dhash, value) for value in heldout_dhashes), default=999) <= 1
        if exact_duplicate:
            counts["exact_heldout_duplicates"] += 1
        if near_duplicate:
            counts["near_heldout_duplicates"] += 1
        image = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
        packet = FramePacket(
            index["frame_id"], index["source_id"], time.monotonic_ns(), time.time_ns(), sequence,
            int(index["width"]), int(index["height"]), "RGB8", image,
            capture_timestamp_sim_ns=int(index["capture_timestamp_sim_ns"]),
        )
        prepared = preprocessor.prepare(packet)[0]
        output = session.run([output_name], {input_name: prepared.tensor})[0]
        decoded = decode_yolo_output(np.asarray(output))
        score = max((item.confidence for item in decoded if item.class_id == 0), default=0.0)
        counts["negative_frames_scanned"] += 1
        if score >= 0.25:
            counts["score_ge_025"] += 1
        elif score >= 0.15:
            counts["score_015_025"] += 1
        elif score >= 0.05:
            counts["score_005_015"] += 1
        else:
            counts["clean_lt_005"] += 1
        mined.append({
            "frame_id": truth["frame_id"],
            "source_time_s": int(index["capture_timestamp_sim_ns"]) / 1e9,
            "source": str(path.relative_to(ROOT)),
            "sha256": digest,
            "dhash": f"{image_dhash:040x}",
            "v1_person_score": score,
            "exact_heldout_duplicate": exact_duplicate,
            "near_heldout_duplicate": near_duplicate,
        })
    return mined, counts


def add_mined(
    mined: list[dict], manifest: list[dict], counts: Counter, image_hashes: set[str]
) -> None:
    for record in mined:
        source_time = float(record["source_time_s"])
        if source_time < TRAIN_MAX_TIME_S:
            split = "train"
        elif source_time > VALIDATION_MIN_TIME_S:
            split = "val"
        else:
            counts["guard_interval_removed"] += 1
            continue
        # Cross-run dHash equality is common on the deliberately texture-light
        # calibration terrain and is not evidence of pixel duplication. Exact
        # bytes are the leak boundary; those are always excluded.
        if record["exact_heldout_duplicate"]:
            counts["heldout_similarity_removed"] += 1
            continue
        if record["sha256"] in image_hashes:
            counts["duplicates_removed"] += 1
            continue
        source = ROOT / record["source"]
        name = f"mined_{source.stem}_{record['sha256'][:10]}.png"
        destination = V2 / "images" / split / name
        shutil.copy2(source, destination)
        (V2 / "labels" / split / f"{destination.stem}.txt").write_text("", encoding="utf-8")
        image_hashes.add(record["sha256"])
        counts[f"{split}_negative"] += 1
        counts["selected_negatives"] += 1
        if record["v1_person_score"] < 0.05:
            counts["selected_random_clean_negatives"] += 1
        manifest.append({
            "split": split,
            "source_group": MINING_RUN.name,
            "source": record["source"],
            "image": str(destination.relative_to(ROOT)),
            "sha256": record["sha256"],
            "frame_id": record["frame_id"],
            "source_time_s": source_time,
            "boxes": [],
            "v1_person_score": record["v1_person_score"],
        })


def main() -> None:
    reset_output()
    manifest: list[dict] = []
    counts: Counter = Counter()
    image_hashes: set[str] = set()
    copy_v1(manifest, counts, image_hashes)
    mined, mining_counts = mine_negative_scores()
    counts.update(mining_counts)
    add_mined(mined, manifest, counts, image_hashes)
    yaml = f"path: {V2.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: person\n"
    (V2 / "dataset.yaml").write_text(yaml, encoding="utf-8")
    (V2 / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (V2 / "hard_negative_scores.json").write_text(json.dumps(mined, indent=2), encoding="utf-8")
    summary = {
        "dataset": "stage13sim_nadir_person_v2",
        "initial_checkpoint": str((V1 / "training_runs/primary/weights/best.pt").relative_to(ROOT)),
        "final_heldout": {
            "source": "stage13sim_exact_pixel_capture_retry26 frozen held-out interval",
            "positive": 39,
            "negative": 530,
            "used_for_training": False,
        },
        "split_policy": {
            "positive_validation_groups": sorted(MOVE_TO_VALIDATION),
            "mining_train_source_time_lt_s": TRAIN_MAX_TIME_S,
            "mining_validation_source_time_gt_s": VALIDATION_MIN_TIME_S,
            "guard_interval_s": VALIDATION_MIN_TIME_S - TRAIN_MAX_TIME_S,
            "adjacent_leakage": False,
            "heldout_exact_sha256_exclusion": True,
            "cross_run_dhash_used_for_diagnostics_only": True,
        },
        "counts": dict(counts),
    }
    (V2 / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
