import json
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

frames = []
for line in open(run_dir / 'vision_events.jsonl'):
    f = json.loads(line)
    if 'header' in f:
        frames.append(f['header'])
    elif 'vision_event' in f and 'header' in f['vision_event']:
        frames.append(f['vision_event']['header'])
    elif 'event' in f and 'header' in f['event']:
        frames.append(f['event']['header'])

# 131044000000 is the first visible frame for hidden_01
target_ns = 131044000000

print(f"Total frames with headers: {len(frames)}")

sorted_frames = sorted(frames, key=lambda x: x.get('sim_capture_time_ns', 0))
for f in sorted_frames:
    if f.get('sim_capture_time_ns', 0) > target_ns - 1000000000 and f.get('sim_capture_time_ns', 0) < target_ns + 3000000000:
        print(f"Time: {f.get('sim_capture_time_ns')} - min={f.get('raw_min')} max={f.get('raw_max')} mean={f.get('raw_mean'):.1f} std={f.get('raw_std'):.1f}")
