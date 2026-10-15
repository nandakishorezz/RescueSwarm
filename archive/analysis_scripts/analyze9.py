import json
from pathlib import Path
from collections import defaultdict

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

observations = []
for line in open(run_dir / 'target_observations.jsonl'):
    observations.append(json.loads(line))

eval_data = json.load(open(run_dir / 'perception_evaluation.json'))
confirmed_fps = eval_data.get('confirmed_false_positive_targets', [])

# Group observations by track ID
tracks = defaultdict(list)
for obs in observations:
    tid = obs.get('target_track_id')
    if tid in confirmed_fps:
        tracks[tid].append(obs)

print("CONFIRMED FALSE POSITIVES:")
for tid, obs_list in tracks.items():
    print(f"\nFP Track {tid}: {len(obs_list)} observations")
    for o in obs_list[:5]:
        print(f"bbox: {o['bbox_original_px']}")
    if len(obs_list) > 5:
        print("...")
        for o in obs_list[-2:]:
            print(f"bbox: {o['bbox_original_px']}")

# Do the same for legitimate targets
print("\nVALID TRACKS:")
valid_tids = [tid for tid in set(o.get('target_track_id') for o in observations) if tid and tid not in confirmed_fps]
tracks = defaultdict(list)
for obs in observations:
    tid = obs.get('target_track_id')
    if tid in valid_tids:
        tracks[tid].append(obs)

for tid, obs_list in tracks.items():
    if len(obs_list) > 10:
        print(f"\nTP Track {tid}: {len(obs_list)} observations")
        for o in obs_list[:5]:
            print(f"bbox: {o['bbox_original_px']}")
        print("...")
        for o in obs_list[-2:]:
            print(f"bbox: {o['bbox_original_px']}")
