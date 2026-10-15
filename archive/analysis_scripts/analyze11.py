import json
from pathlib import Path
run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')
eval_data = json.load(open(run_dir / 'perception_evaluation.json'))
count = 0
for f in eval_data.get('frames', []):
    for tb in f.get('truth_boxes', []):
        if tb.get('survivor_id') == 'hidden_01':
            if count < 10 or count > 80:
                print(f"Frame {f['frame_id']}: bbox={tb['bbox_pixels']}, occluded={tb.get('occluded')}, clipped={tb.get('is_clipped_by_border')}")
            count += 1
print(f"Total hidden_01 frames: {count}")
