import json
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

poses = [json.loads(line) for line in open(run_dir / 'SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl')]
def get_pose(sim_s):
    for p in poses:
        if p.get('sim_time_s', 0) >= sim_s:
            return p
    return poses[-1]

p = get_pose(131.044)
print(f"Drone ENU at 131s: {p['vehicle_position_gazebo_m']}")
