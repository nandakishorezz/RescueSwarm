import json
from pathlib import Path
import math

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

observations = []
for line in open(run_dir / 'target_observations.jsonl'):
    observations.append(json.loads(line))

eval_data = json.load(open(run_dir / 'perception_evaluation.json'))

confirmed_fps = eval_data.get('confirmed_false_positive_targets', [])

print("Confirmed False Positives:")
for tid in confirmed_fps:
    obs = [o for o in observations if o.get('target_track_id') == tid or o.get('observation_id') == tid]
    # Actually, observations might not have target_track_id linked directly if they are raw.
    # Let's group observations by their track ID from confirmed_target_history.jsonl
    pass

history = []
for line in open(run_dir / 'confirmed_target_history.jsonl'):
    history.append(json.loads(line))

for tid in confirmed_fps:
    track_hist = [h for h in history if h['target_id'] == tid]
    if track_hist:
        first = track_hist[0]
        last = track_hist[-1]
        dist = math.hypot(last['east_m'] - first['east_m'], last['north_m'] - first['north_m'])
        print(f"FP Track {tid}: {len(track_hist)} updates, moved {dist:.1f}m")

# Legitimate tracks
valid_tids = []
for h in history:
    if h['target_id'] not in confirmed_fps and h['target_id'] not in valid_tids:
        valid_tids.append(h['target_id'])

for tid in valid_tids:
    track_hist = [h for h in history if h['target_id'] == tid]
    if track_hist:
        first = track_hist[0]
        last = track_hist[-1]
        dist = math.hypot(last['east_m'] - first['east_m'], last['north_m'] - first['north_m'])
        print(f"TP Track {tid}: {len(track_hist)} updates, moved {dist:.1f}m")

