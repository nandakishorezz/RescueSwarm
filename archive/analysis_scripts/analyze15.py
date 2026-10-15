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

def is_marginal(x1, y1, x2, y2, margin):
    return x1 < margin or y1 < margin or x2 > (1280 - margin) or y2 > (800 - margin)

for margin in [15, 25, 35, 50, 75]:
    print(f"\nMargin = {margin}")
    for tid in confirmed_fps:
        obs_list = [o for o in observations if o.get('target_track_id') == tid]
        clear_obs = [o for o in obs_list if not is_marginal(*o['bbox_original_px'], margin)]
        if clear_obs:
            print(f"FP {tid} has {len(clear_obs)} clear obs out of {len(obs_list)}")
            for o in clear_obs[:2]:
                print(f"  Clear: {o['bbox_original_px']}")

    # also check true positives!
    print(f"True Positives clearing margin {margin}:")
    valid_tids = [tid for tid in set(o.get('target_track_id') for o in observations) if tid and tid not in confirmed_fps]
    for tid in valid_tids:
        obs_list = [o for o in observations if o.get('target_track_id') == tid]
        if not obs_list: continue
        clear_obs = [o for o in obs_list if not is_marginal(*o['bbox_original_px'], margin)]
        if not clear_obs:
            print(f"WARNING: TP {tid} has 0 clear obs! (Total {len(obs_list)})")
            for o in obs_list[:2]:
                print(f"  Marginal: {o['bbox_original_px']}")
