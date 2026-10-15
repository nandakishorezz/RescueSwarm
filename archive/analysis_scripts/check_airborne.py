import json
import math

def check_airborne(run_dir):
    print(f"--- {run_dir} ---")
    estimates = []
    with open(f"{run_dir}/autopilot_estimate.jsonl", 'r') as f:
        for line in f:
            estimates.append(json.loads(line))
            
    for e in estimates:
        t = e.get("ap_sim_source_time_ns", 0) / 1e9
        alt = e.get("relative_altitude_m", 0)
        v = e.get("velocity_ned_mps", [0, 0, 0])
        speed = math.hypot(v[0], v[1])
        if alt >= 6.0 and speed >= 0.5:
            print(f"Trigger condition reached at sim time {t}s (alt={alt}, speed={speed})")
            break

check_airborne("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28")
check_airborne("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke")
