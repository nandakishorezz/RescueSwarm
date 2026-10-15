import json
import numpy as np
import os

def load_jsonl(path):
    with open(path, 'r') as f:
        return [json.loads(line) for line in f]

def get_time_of_closest_approach(run_dir):
    autopilot_path = os.path.join(run_dir, "autopilot_estimate.jsonl")
    survivor_path = os.path.join(run_dir, "SIMULATION_ONLY_GROUND_TRUTH_survivors.json")
    
    with open(survivor_path, 'r') as f:
        survivor_data = json.load(f)
        
    surv_wgs84 = np.array(survivor_data["survivors"][0]["position_wgs84"])
    
    estimates = load_jsonl(autopilot_path)
    
    min_horizontal_dist = float('inf')
    best_time = None
    
    for est in estimates:
        lat = est["latitude"]
        lon = est["longitude"]
        d_lat_m = (lat - surv_wgs84[0]) * 111320.0
        d_lon_m = (lon - surv_wgs84[1]) * (111320.0 * np.cos(np.radians(surv_wgs84[0])))
        horiz_dist = np.sqrt(d_lat_m**2 + d_lon_m**2)
        if horiz_dist < min_horizontal_dist:
            min_horizontal_dist = horiz_dist
            best_time = est.get("ap_sim_source_time_ns", 0) / 1e9
            
    return best_time, min_horizontal_dist

t_retry28, dist_retry28 = get_time_of_closest_approach("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28")
print(f"retry28 closest approach at {t_retry28}s, dist={dist_retry28}m")

t_smoke, dist_smoke = get_time_of_closest_approach("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke")
print(f"smoke closest approach at {t_smoke}s, dist={dist_smoke}m")

# Also find evaluator start and camera start in retry28 summary
with open("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28/phase2_run_summary.json") as f:
    s = json.load(f)
    print("retry28 first sim time ns:", s["transport"]["first_sim_time_ns"])

with open("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke/phase2_run_summary.json") as f:
    s = json.load(f)
    print("smoke first sim time ns:", s["transport"]["first_sim_time_ns"])
