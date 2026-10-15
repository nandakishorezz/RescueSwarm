import json
import math
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

observations = {}
for line in open(run_dir / 'target_observations.jsonl'):
    o = json.loads(line)
    observations[o['observation_id']] = o

links = json.load(open(run_dir / 'observation_detection_links.json'))
# links is a dict: observation_id -> [frame_id, detection_index]
# reverse it
frame_det_to_obs = {}
for obs_id, (fid, d_idx) in links.items():
    frame_det_to_obs[(fid, d_idx)] = obs_id

eval_data = json.load(open(run_dir / 'perception_evaluation.json'))

confirmed_fps = eval_data.get('confirmed_false_positive_targets', [])
fps = []
tps = []

dets_by_frame = {json.loads(line)['frame_id']: json.loads(line) for line in open(run_dir / 'perception_detections.jsonl')}

for f in eval_data.get('frames', []):
    fid = f['frame_id']
    if fid in dets_by_frame:
        if f.get('false_positive_indices'):
            for idx in f['false_positive_indices']:
                fps.append((fid, idx))
        if f.get('matches'):
            for m in f['matches']:
                tps.append((fid, m['detection_index']))

print("False Positive Geolocation relative to drone:")
for fid, idx in fps:
    obs_id = frame_det_to_obs.get((fid, idx))
    if obs_id and obs_id in observations:
        o = observations[obs_id]
        te, tn = o['target_east_m'], o['target_north_m']
        de, dn, _ = o['interpolated_pose']['sample']['local_position_ned_m']
        dist = math.hypot(te - de, tn - dn)
        print(f"FP: Distance from drone to target: {dist:.1f}m")

print("\nTrue Positive Geolocation relative to drone:")
for fid, idx in tps[:20]:
    obs_id = frame_det_to_obs.get((fid, idx))
    if obs_id and obs_id in observations:
        o = observations[obs_id]
        te, tn = o['target_east_m'], o['target_north_m']
        de, dn, _ = o['interpolated_pose']['sample']['local_position_ned_m']
        dist = math.hypot(te - de, tn - dn)
        print(f"TP: Distance from drone to target: {dist:.1f}m")
