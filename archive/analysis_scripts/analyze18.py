import json
import math
from pathlib import Path

run_dir = Path('data/simulation_runs/phase4_qualification_single_drone_intelligence/stage13sim_phase2_20260928T170730Z')

poses = [json.loads(line) for line in open(run_dir / 'autopilot_estimate.jsonl')]
def get_pose(sim_ns):
    for p in poses:
        if p.get('ap_sim_source_time_ns', 0) >= sim_ns:
            return p
    return poses[-1]

def euler_from_quaternion(x, y, z, w):
    t0 = +2.0 * (w * x + y * z)
    t1 = +1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(t0, t1)
    
    t2 = +2.0 * (w * y - z * x)
    t2 = +1.0 if t2 > +1.0 else t2
    t2 = -1.0 if t2 < -1.0 else t2
    pitch = math.asin(t2)
    
    t3 = +2.0 * (w * z + x * y)
    t4 = +1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(t3, t4)
    return roll, pitch, yaw

for s_id, t_ns in [('hidden_01', 131044000000), ('hidden_02', 133981000000), ('hidden_03', 134113000000)]:
    p = get_pose(t_ns)
    q = p['quaternion_body_to_ned']
    roll, pitch, yaw = euler_from_quaternion(q[1], q[2], q[3], q[0])
    
    speed = math.hypot(p['velocity_ned_mps'][0], p['velocity_ned_mps'][1])
    print(f"{s_id}: Pitch={math.degrees(pitch):.1f}deg, Roll={math.degrees(roll):.1f}deg, Speed={speed:.1f}m/s, Alt={p['relative_altitude_m']:.1f}m")
