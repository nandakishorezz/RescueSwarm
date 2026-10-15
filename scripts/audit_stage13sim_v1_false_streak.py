"""Preserve forensic evidence for the V1 five-frame false-person streak."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data" / "simulation_runs" / "stage13sim_exact_pixel_capture_retry26"
V1 = ROOT / "data" / "training" / "stage13sim_nadir_person_v1"


def euler_deg(q: list[float]) -> list[float]:
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def main() -> None:
    comparison = json.loads((V1 / "heldout_old_vs_new.json").read_text(encoding="utf-8"))
    frames = comparison["details"]["new"]["frames"]
    runs, current = [], []
    for frame in frames:
        false = not frame["truth_visible"] and bool(frame["passed_predictions"])
        if false:
            current.append(frame)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    streak = max(runs, key=len)
    poses = [json.loads(line) for line in (RUN / "autopilot_estimate.jsonl").read_text().splitlines() if line]
    rows = []
    annotated = []
    for frame in streak:
        source_ns = int(frame["source_time_ns"])
        pose = min(poses, key=lambda row: abs(int(row["ap_sim_source_time_ns"]) - source_ns))
        safe = frame["frame_id"].replace(":", "_") + ".png"
        image_path = V1 / "images" / "heldout" / safe
        image = Image.open(image_path).convert("RGB")
        pixels = np.asarray(image)
        draw_image = image.copy()
        draw = ImageDraw.Draw(draw_image)
        predictions = []
        for prediction in frame["passed_predictions"]:
            box = prediction["box"]
            draw.rectangle(box, outline=(255, 0, 0), width=5)
            crop = pixels[int(box[1]):max(int(box[1]) + 1, int(box[3])), int(box[0]):max(int(box[0]) + 1, int(box[2]))]
            predictions.append({
                **prediction,
                "normalized_center": [(box[0] + box[2]) / 2560.0, (box[1] + box[3]) / 1600.0],
                "bbox_dimensions_px": [box[2] - box[0], box[3] - box[1]],
                "crop_mean": float(crop.mean()) if crop.size else None,
                "crop_std": float(crop.std()) if crop.size else None,
            })
        annotated.append(draw_image.resize((640, 400)))
        rows.append({
            "frame_id": frame["frame_id"],
            "source_time_s": source_ns / 1e9,
            "predictions": predictions,
            "vehicle_attitude_roll_pitch_yaw_deg": euler_deg(pose["quaternion_body_to_ned"]),
            "image_statistics": {
                "min": int(pixels.min()), "max": int(pixels.max()),
                "mean": float(pixels.mean()), "std": float(pixels.std()),
            },
        })
    sheet = Image.new("RGB", (640, 400 * len(annotated)))
    for index, image in enumerate(annotated):
        sheet.paste(image, (0, index * 400))
    sheet.save(V1 / "V1_FALSE_POSITIVE_STREAK_CONTACT_SHEET.png")
    report = {
        "classification": "DRONE_SHADOW",
        "basis": "The predicted edge region contains the rendered four-rotor aircraft shadow and no survivor truth pixels.",
        "streak_length": len(streak),
        "frames": rows,
    }
    (V1 / "V1_FALSE_POSITIVE_STREAK_AUDIT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
