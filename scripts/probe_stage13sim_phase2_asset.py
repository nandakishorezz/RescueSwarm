"""Live pixel-only control for the licensed survivor asset (not the gold run)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from PIL import Image

from rescue_core.simulation.perception import GazeboCameraSource
from rescue_core.simulation.evaluator import GazeboSegmentationSource, semantic_mask_boxes
from rescue_core.simulation.gazebo.processes import windows_to_wsl
from rescue_core.vision.factory import create_detector
from rescue_core.vision.config import load_vision_config

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "simulation" / "phase2_asset_probe")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = windows_to_wsl(ROOT / "simulation" / "worlds" / "perception_asset_control.sdf")
    assets = windows_to_wsl(ROOT / "simulation" / "assets")
    launch_script = args.output / "launch_asset_control.sh"
    pid_file = args.output / "gazebo.pid"
    pid_file.unlink(missing_ok=True)
    launch_script.write_bytes((
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"export GZ_SIM_RESOURCE_PATH={assets}\n"
        f"echo $$ > {windows_to_wsl(pid_file)}\n"
        f"exec gz sim -s -r --headless-rendering {world}\n"
    ).encode("utf-8"))
    source = GazeboCameraSource("/phase2/control/image")
    segmentation = GazeboSegmentationSource("/phase2/control/evaluator_segmentation")
    detector = create_detector(load_vision_config(), backend="onnx", model_id="yolo11n_person_onnx")
    process = subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu", "--", "bash", windows_to_wsl(launch_script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    results = []
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            topics = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "gz", "topic", "-l"],
                capture_output=True, text=True,
            ).stdout
            if "/phase2/control/image" in topics:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Gazebo control camera topic did not appear")
        source.start(); detector.load(); segmentation.start()
        truth_boxes = []
        for index in range(args.frames):
            frame = source.read()
            if index == 0:
                Image.fromarray(frame.image).save(args.output / "AUTONOMY_INPUT_MONO8.png")
            if index == args.frames - 1:
                Image.fromarray(frame.image).save(args.output / "AUTONOMY_INPUT_LAST_MONO8.png")
            result = detector.detect(frame)
            results.append(result.as_dict())
            # Evaluator-only subscription is consumed after inference.
            truth_frame = segmentation.read()
            truth_boxes = semantic_mask_boxes(truth_frame, survivor_labels=frozenset({101}))
            if index == 0:
                Image.fromarray(truth_frame.label_image.astype("uint8")).save(args.output / "SIMULATION_TRUTH_MASK.png")
            if result.detections:
                Image.fromarray(frame.image).save(args.output / "AUTONOMY_INPUT_DETECTED_MONO8.png")
        summary = {
            "control_only_not_gold": True,
            "frames": len(results),
            "person_detections": sum(len(item["detections"]) for item in results),
            "transport": source.metrics.as_dict(),
            "semantic_label_ids": [box.evaluator_label for box in truth_boxes],
            "visible_survivor_pixels": sum(box.visible_pixels for box in truth_boxes),
            "model_hash": detector.model.sha256,
            "results": results,
        }
        (args.output / "probe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=2))
        return 0 if summary["person_detections"] else 2
    finally:
        source.stop(); segmentation.stop(); detector.close()
        if pid_file.exists():
            subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "kill", pid_file.read_text(encoding="ascii").strip()],
                capture_output=True, text=True,
            )
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
