import json
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

target_start = 131000000000
target_end = 135000000000

for line in open(run_dir / 'perception_detections.jsonl'):
    d = json.loads(line)
    sim_ns = d.get('capture_timestamp_sim_ns', 0)
    if target_start <= sim_ns <= target_end:
        print(f"Time {sim_ns}: min={d.get('raw_min')}, max={d.get('raw_max')}, mean={d.get('raw_mean')}")

