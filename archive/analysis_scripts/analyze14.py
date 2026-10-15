import json
import numpy as np
from pathlib import Path
from collections import defaultdict

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

observations = []
for line in open(run_dir / 'target_observations.jsonl'):
    observations.append(json.loads(line))

eval_data = json.load(open(run_dir / 'perception_evaluation.json'))
confirmed_fps = eval_data.get('confirmed_false_positive_targets', [])

valid_tids = [tid for tid in set(o.get('target_track_id') for o in observations) if tid and tid not in confirmed_fps]

def print_pixel_variance(tids, name):
    tracks = defaultdict(list)
    for obs in observations:
        if obs.get('target_track_id') in tids:
            tracks[obs['target_track_id']].append(obs)
            
    for tid, obs_list in tracks.items():
        if len(obs_list) >= 5:
            centers_x = [(o['bbox_original_px'][0] + o['bbox_original_px'][2])/2 for o in obs_list]
            centers_y = [(o['bbox_original_px'][1] + o['bbox_original_px'][3])/2 for o in obs_list]
            var_x = np.std(centers_x)
            var_y = np.std(centers_y)
            drift = np.hypot(centers_x[-1] - centers_x[0], centers_y[-1] - centers_y[0])
            print(f"{name} Track {tid}: {len(obs_list)} obs. Pixel StdDev: x={var_x:.1f}, y={var_y:.1f}. Total Drift: {drift:.1f}px")

print_pixel_variance(confirmed_fps, "FP")
print("\n")
print_pixel_variance(valid_tids, "TP")
