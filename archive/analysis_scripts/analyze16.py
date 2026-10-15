import json
import math
import numpy as np
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

poses = [json.loads(line) for line in open(run_dir / 'autopilot_estimate.jsonl')]
def get_pose(sim_ns):
    for p in poses:
        if p.get('ap_sim_source_time_ns', 0) >= sim_ns:
            return p
    return poses[-1]

survivors = json.load(open(run_dir / 'SIMULATION_ONLY_GROUND_TRUTH_survivors.json'))['survivors']
s_pos = {s['survivor_id']: s['position_enu_m'] for s in survivors}

for s_id, t_ns in [('hidden_01', 131044000000), ('hidden_03', 134113000000)]:
    pos = s_pos[s_id]
    p = get_pose(t_ns)
    
    # Enu to Wgs84 is not needed if we use east_m, north_m.
    # But autopilot_estimate only has latitude/longitude?
    # No, we need east_m/north_m from autopilot_estimate.jsonl ? Wait, it doesn't have it!
    # Let's just print latitude/longitude.
    print(f"{s_id}: Alt={p['relative_altitude_m']:.1f}m, Pos=({p['latitude']}, {p['longitude']})")

