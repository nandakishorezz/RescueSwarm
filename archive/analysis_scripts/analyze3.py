import json
from pathlib import Path
run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')
dets_by_frame = {json.loads(line)['frame_id']: json.loads(line) for line in open(run_dir / 'perception_detections.jsonl')}
eval_data = json.load(open(run_dir / 'perception_evaluation.json'))
tps = []
for f in eval_data.get('frames', []):
    fid = f['frame_id']
    if fid in dets_by_frame and f.get('matches'):
        for m in f['matches']:
            tps.append(dets_by_frame[fid]['detections'][m['detection_index']])
print(f'Total TPs: {len(tps)}')
max_area = max(d['bbox_area_px'] for d in tps)
min_area = min(d['bbox_area_px'] for d in tps)
print(f'TP Area: min={min_area:.1f}, max={max_area:.1f}')
for d in tps[:5]:
    print(f"y1={d['bbox_pixels'][1]:.1f} y2={d['bbox_pixels'][3]:.1f} x1={d['bbox_pixels'][0]:.1f} x2={d['bbox_pixels'][2]:.1f} area={d['bbox_area_px']:.1f}")
