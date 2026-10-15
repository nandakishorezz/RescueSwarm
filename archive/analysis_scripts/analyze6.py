import json
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

observations = []
for line in open(run_dir / 'target_observations.jsonl'):
    observations.append(json.loads(line))

for obs in observations[:10]:
    if obs['detection_confidence'] < 0.5:
        # Check what the speed of the drone was
        pass

eval_data = json.load(open(run_dir / 'perception_evaluation.json'))
fps = []
dets_by_frame = {json.loads(line)['frame_id']: json.loads(line) for line in open(run_dir / 'perception_detections.jsonl')}

for f in eval_data.get('frames', []):
    fid = f['frame_id']
    if fid in dets_by_frame and f.get('false_positive_indices'):
        for idx in f['false_positive_indices']:
            fps.append((fid, dets_by_frame[fid]['detections'][idx]))

for fid, d in fps[:5]:
    # find observation corresponding to this frame
    obs = [o for o in observations if o['frame_id'] == fid]
    for o in obs:
        vel = o['interpolated_pose']['sample']['velocity_ned_m_s']
        print(f"FP in frame {fid}: drone velocity {vel}")
