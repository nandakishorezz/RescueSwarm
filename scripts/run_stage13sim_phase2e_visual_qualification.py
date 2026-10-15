"""Short live rendered Phase-2E FOV, occlusion, pitch, and altitude controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from rescue_core.simulation.evaluator import GazeboSegmentationSource, semantic_mask_boxes
from rescue_core.simulation.gazebo.processes import windows_to_wsl
from rescue_core.simulation.perception import GazeboCameraSource
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.factory import create_detector

ROOT = Path(__file__).resolve().parents[1]
IMAGE_TOPIC = "/phase2e/control/image"
SEGMENTATION_TOPIC = "/phase2e/control/evaluator_segmentation"


def _world(*, name: str, survivor: tuple[float, float], camera_height: float,
           camera_pitch_deg: float, occluder_width: float = 0.0) -> str:
    occluder = ""
    if occluder_width > 0:
        occluder = f"""
    <model name='occluder'><static>true</static><pose>2.5 0 1.0 0 0 0</pose><link name='wall'>
      <collision name='collision'><geometry><box><size>.30 {occluder_width} 2.0</size></box></geometry></collision>
      <visual name='visual'><geometry><box><size>.30 {occluder_width} 2.0</size></box></geometry><material><diffuse>.35 .35 .35 1</diffuse></material></visual>
    </link></model>"""
    pitch = math.radians(camera_pitch_deg)
    return f"""<?xml version='1.0'?>
<sdf version='1.9'><world name='{name}'>
  <plugin filename='gz-sim-physics-system' name='gz::sim::systems::Physics'/>
  <plugin filename='gz-sim-user-commands-system' name='gz::sim::systems::UserCommands'/>
  <plugin filename='gz-sim-scene-broadcaster-system' name='gz::sim::systems::SceneBroadcaster'/>
  <plugin filename='gz-sim-sensors-system' name='gz::sim::systems::Sensors'><render_engine>ogre2</render_engine></plugin>
  <scene><ambient>.45 .45 .45 1</ambient><background>.62 .76 .88 1</background><shadows>true</shadows></scene>
  <light name='sun' type='directional'><direction>-.4 .2 -.9</direction><diffuse>.9 .9 .9 1</diffuse><cast_shadows>true</cast_shadows></light>
  <model name='ground'><static>true</static><link name='link'><collision name='c'><geometry><box><size>40 40 .1</size></box></geometry></collision><visual name='v'><geometry><box><size>40 40 .1</size></box></geometry><material><diffuse>.23 .34 .20 1</diffuse></material></visual></link></model>
  <model name='survivor'><static>true</static><pose>{survivor[0]} {survivor[1]} 0 0 0 3.14159265</pose><link name='person'><visual name='human'><geometry><mesh><uri>model://gazebo_actor/meshes/stand.dae</uri></mesh></geometry></visual></link><plugin filename='gz-sim-label-system' name='gz::sim::systems::Label'><label>101</label></plugin></model>
  {occluder}
  <model name='camera_rig'><static>true</static><pose>0 0 {camera_height} 0 0 0</pose><link name='camera_link'><pose>0 0 0 0 {pitch} 0</pose>
    <sensor name='autonomy_camera' type='camera'><topic>{IMAGE_TOPIC}</topic><always_on>true</always_on><update_rate>30</update_rate><camera><horizontal_fov>1.3494818844471055</horizontal_fov><image><width>1280</width><height>800</height><format>L_INT8</format></image><clip><near>.05</near><far>100</far></clip></camera></sensor>
    <sensor name='evaluator_camera' type='segmentation_camera'><topic>{SEGMENTATION_TOPIC}</topic><always_on>true</always_on><update_rate>30</update_rate><camera><segmentation_type>semantic</segmentation_type><horizontal_fov>1.3494818844471055</horizontal_fov><image><width>1280</width><height>800</height></image><clip><near>.05</near><far>100</far></clip></camera></sensor>
  </link></model>
</world></sdf>\n"""


def _run_case(output: Path, detector, *, case: str, survivor: tuple[float, float],
              camera_height: float, camera_pitch_deg: float, occluder_width: float = 0.0,
              frames: int = 4) -> dict[str, object]:
    case_dir = output / case.lower()
    case_dir.mkdir(parents=True, exist_ok=True)
    sdf = case_dir / "world.sdf"
    sdf.write_text(_world(name=f"phase2e_{case.lower()}", survivor=survivor,
                          camera_height=camera_height, camera_pitch_deg=camera_pitch_deg,
                          occluder_width=occluder_width), encoding="utf-8")
    assets = windows_to_wsl(ROOT / "simulation" / "assets")
    proc = subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc",
         f"export GZ_SIM_RESOURCE_PATH='{assets}'; exec gz sim -s -r --headless-rendering '{windows_to_wsl(sdf)}'"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    source = GazeboCameraSource(IMAGE_TOPIC)
    truth_source = GazeboSegmentationSource(SEGMENTATION_TOPIC)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            topics = subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "gz", "topic", "-l"],
                                    capture_output=True, text=True).stdout
            if IMAGE_TOPIC in topics and SEGMENTATION_TOPIC in topics:
                break
            if proc.poll() is not None:
                raise RuntimeError(f"Gazebo exited for {case}")
            time.sleep(.2)
        else:
            raise RuntimeError(f"camera topics missing for {case}")
        source.start(); truth_source.start()
        rows: list[dict[str, object]] = []
        for index in range(frames):
            frame = source.read()
            result = detector.detect(frame)
            truth_frame = truth_source.read()
            boxes = semantic_mask_boxes(truth_frame, survivor_labels=frozenset({101}))
            box = max(boxes, key=lambda value: value.visible_pixels) if boxes else None
            if index == frames - 1:
                Image.fromarray(frame.image).save(case_dir / "AUTONOMY_INPUT_MONO8.png")
                Image.fromarray(truth_frame.label_image.astype("uint8")).save(case_dir / "EVALUATOR_LABELS.png")
            rows.append({
                "frame_id": frame.frame_id,
                "capture_sim_ns": frame.capture_timestamp_sim_ns,
                "visible_pixels": box.visible_pixels if box else 0,
                "truth_bbox": [box.x1, box.y1, box.x2, box.y2] if box else None,
                "edge_clipped": box.edge_clipped if box else False,
                "yolo_result": bool(result.detections),
                "yolo_confidence": max((item.confidence for item in result.detections), default=None),
                "yolo_bbox": ([result.detections[0].x1, result.detections[0].y1,
                               result.detections[0].x2, result.detections[0].y2]
                              if result.detections else None),
            })
        representative = max(rows, key=lambda item: int(item["visible_pixels"]))
        return {
            "case": case,
            "survivor_truth_position_enu_m": [survivor[0], survivor[1], 0.0],
            "camera_pose": {"position_enu_m": [0.0, 0.0, camera_height],
                            "sdf_pitch_deg_down": camera_pitch_deg},
            "occluder_width_m": occluder_width,
            **{key: representative[key] for key in ("visible_pixels", "truth_bbox", "edge_clipped",
                                                     "yolo_result", "yolo_confidence", "yolo_bbox")},
            "frames": rows,
            "autonomy_received": "CAMERA_PIXELS_ONLY",
        }
    finally:
        source.stop(); truth_source.stop()
        proc.terminate()
        try: proc.wait(8)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(3)
        subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc",
                        "pkill -f 'gz sim.*phase2e_' || true"], capture_output=True)
        time.sleep(.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    detector = create_detector(load_vision_config(ROOT / "config" / "vision.yaml"),
                               backend="onnx", model_id="yolo11n_person_onnx")
    detector.load()
    try:
        fov = [
            _run_case(args.output, detector, case="FOV_INSIDE", survivor=(5.0, 0.0), camera_height=1.15, camera_pitch_deg=0),
            _run_case(args.output, detector, case="FOV_EDGE", survivor=(5.0, 3.9), camera_height=1.15, camera_pitch_deg=0),
            _run_case(args.output, detector, case="FOV_OUTSIDE", survivor=(5.0, 5.0), camera_height=1.15, camera_pitch_deg=0),
        ]
        fov_report = {
            "classification": "EVALUATOR_ONLY_VISIBILITY_AFTER_AUTONOMY_INFERENCE",
            "camera_profile": "OV9281_REFERENCE_1280x800_30HZ",
            "model_sha256": detector.model.sha256,
            "cases": fov,
            "passed": (fov[0]["visible_pixels"] > 0 and fov[1]["visible_pixels"] > 0
                       and fov[1]["edge_clipped"] is True and fov[2]["visible_pixels"] == 0),
        }
        (args.output / "FOV_QUALIFICATION.json").write_text(json.dumps(fov_report, indent=2) + "\n")

        occlusion = [
            _run_case(args.output, detector, case="VISIBLE", survivor=(5.0, 0.0), camera_height=1.15, camera_pitch_deg=0),
            _run_case(args.output, detector, case="PARTIALLY_OCCLUDED", survivor=(5.0, 0.0), camera_height=1.15, camera_pitch_deg=0, occluder_width=.08),
            _run_case(args.output, detector, case="FULLY_OCCLUDED", survivor=(5.0, 0.0), camera_height=1.15, camera_pitch_deg=0, occluder_width=1.4),
        ]
        baseline = max(1, int(occlusion[0]["visible_pixels"]))
        for item in occlusion:
            item["visible_fraction"] = int(item["visible_pixels"]) / baseline
        occ_report = {
            "classification": "EVALUATOR_ONLY_OCCLUSION_AFTER_AUTONOMY_INFERENCE",
            "actual_gazebo_geometry": True,
            "detector_side_occlusion_logic": False,
            "cases": occlusion,
            "passed": (occlusion[0]["visible_fraction"] > .95
                       and 0 < occlusion[1]["visible_fraction"] < .95
                       and occlusion[2]["visible_pixels"] == 0),
        }
        (args.output / "OCCLUSION_QUALIFICATION.json").write_text(json.dumps(occ_report, indent=2) + "\n")

        pitch = [
            _run_case(args.output, detector, case="PITCH_35_FROM_NADIR", survivor=(7.0, 0.0), camera_height=8, camera_pitch_deg=55),
            _run_case(args.output, detector, case="PITCH_45_FROM_NADIR", survivor=(7.0, 0.0), camera_height=8, camera_pitch_deg=45),
        ]
        for item in pitch:
            box = item["truth_bbox"]
            item["person_height_px"] = box[3] - box[1] if box else 0
            angle = 90 - float(item["camera_pose"]["sdf_pitch_deg_down"])
            item["ground_footprint_center_forward_m"] = 8 * math.tan(math.radians(angle))
        pitch_report = {
            "controlled_constants": ["survivor", "altitude", "lighting", "detector"],
            "changed_only": "camera_mount_pitch",
            "cases": pitch,
            "passed": all(item["visible_pixels"] > 0 for item in pitch)
                      and pitch[0]["truth_bbox"] != pitch[1]["truth_bbox"],
        }
        (args.output / "CAMERA_PITCH_EXPERIMENT.json").write_text(json.dumps(pitch_report, indent=2) + "\n")

        altitude = [
            _run_case(args.output, detector, case="ALTITUDE_6M", survivor=(6.0, 0.0), camera_height=6, camera_pitch_deg=55),
            _run_case(args.output, detector, case="ALTITUDE_10M", survivor=(6.0, 0.0), camera_height=10, camera_pitch_deg=55),
        ]
        for item in altitude:
            box = item["truth_bbox"]
            item["person_height_px"] = box[3] - box[1] if box else 0
            item["bbox_area_px"] = ((box[2] - box[0]) * (box[3] - box[1])) if box else 0
            height = float(item["camera_pose"]["position_enu_m"][2])
            item["footprint_dimensions_m"] = [2 * height * math.tan(1.3494818844471055 / 2),
                                               2 * height * math.tan(.9272952180016122 / 2)]
        altitude_report = {
            "controlled_constants": ["survivor", "mount_pitch", "lighting", "detector"],
            "changed_only": "altitude",
            "cases": altitude,
            "passed": all(item["visible_pixels"] > 0 for item in altitude)
                      and altitude[0]["person_height_px"] > altitude[1]["person_height_px"],
        }
        (args.output / "ALTITUDE_EXPERIMENT.json").write_text(json.dumps(altitude_report, indent=2) + "\n")
        manifest = {
            "passed": all((fov_report["passed"], occ_report["passed"], pitch_report["passed"], altitude_report["passed"])),
            "world_hashes": {str(path.relative_to(args.output)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in args.output.rglob("world.sdf")},
        }
        (args.output / "visual_qualification_summary.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
        return 0 if manifest["passed"] else 2
    finally:
        detector.close()


if __name__ == "__main__":
    raise SystemExit(main())
