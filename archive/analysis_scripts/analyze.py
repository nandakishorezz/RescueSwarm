import json
import sys
from pathlib import Path

run_dir = Path("data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z")

# Load detections
detections_by_frame = {}
with open(run_dir / "perception_detections.jsonl", "r") as f:
    for line in f:
        d = json.loads(line)
        detections_by_frame[d["frame_id"]] = d

# Load eval
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
                false_positives.append((fid, dets[fp_idx]))
                
    for truth in frame.get('truth_boxes', []):
        if truth.get('survivor_id') == 'hidden_01':
            hidden_01_frames.append((fid, truth, frame))

print(f"Found {len(false_positives)} false positive detections")
print(f"Found {len(hidden_01_frames)} frames with hidden_01")

if false_positives:
    print("\nSample False Positives:")
    for fid, d in false_positives[:10]:
        print(f"{fid}: {d}")

if hidden_01_frames:
    print("\nSample hidden_01 frames:")
    for fid, t, frame_info in hidden_01_frames[:5]:
        print(f"Frame ID: {fid} - Truth: {t}")
        if frame_info.get('matches'):
            print(f"  Matches: {frame_info['matches']}")
