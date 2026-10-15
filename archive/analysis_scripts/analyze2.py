import json
import sys
from pathlib import Path

run_dir = Path("data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z")

detections_by_frame = {}
with open(run_dir / "perception_detections.jsonl", "r") as f:
    for line in f:
        d = json.loads(line)
        detections_by_frame[d["frame_id"]] = d

eval_data = json.load(open(run_dir / "perception_evaluation.json"))

false_positives = []
hidden_01_frames = []

for frame in eval_data.get('frames', []):
    fid = frame['frame_id']
    fps = frame.get('false_positive_indices', [])
    
    if fid in detections_by_frame and fps:
        dets = detections_by_frame[fid].get('detections', [])
        for fp_idx in fps:
            if fp_idx < len(dets):
                false_positives.append(dets[fp_idx])
                
    for truth in frame.get('truth_boxes', []):
        if truth.get('survivor_id') == 'hidden_01':
            hidden_01_frames.append((fid, truth, frame))

print(f"Total false positives: {len(false_positives)}")
for name, condition in [
    ("touches_top (y1 < 5)", lambda d: d['bbox_pixels'][1] < 5),
    ("touches_bottom (y2 > 795)", lambda d: d['bbox_pixels'][3] > 795),
    ("touches_left (x1 < 5)", lambda d: d['bbox_pixels'][0] < 5),
    ("touches_right (x2 > 1275)", lambda d: d['bbox_pixels'][2] > 1275),
    ("huge_area (> 40000)", lambda d: d['bbox_area_px'] > 40000),
    ("high_confidence (> 0.5)", lambda d: d['confidence'] > 0.5),
]:
    count = sum(1 for d in false_positives if condition(d))
    print(f"{name}: {count}")

print("\nFalse Positives not touching any border:")
for d in false_positives:
    if not (d['bbox_pixels'][1] < 5 or d['bbox_pixels'][3] > 795 or d['bbox_pixels'][0] < 5 or d['bbox_pixels'][2] > 1275):
        print(f"y1={d['bbox_pixels'][1]} y2={d['bbox_pixels'][3]} x1={d['bbox_pixels'][0]} x2={d['bbox_pixels'][2]} conf={d['confidence']}")

