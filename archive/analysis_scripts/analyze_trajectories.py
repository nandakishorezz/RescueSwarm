import json
import numpy as np
import os

def load_jsonl(path):
    with open(path, 'r') as f:
        return [json.loads(line) for line in f]

def analyze_run(run_dir):
    print(f"Analyzing {run_dir}")
    autopilot_path = os.path.join(run_dir, "autopilot_estimate.jsonl")
    survivor_path = os.path.join(run_dir, "SIMULATION_ONLY_GROUND_TRUTH_survivors.json")
    
    with open(survivor_path, 'r') as f:
        survivor_data = json.load(f)
        
    surv_wgs84 = np.array(survivor_data["survivors"][0]["position_wgs84"])
    # [lat, lon, alt]
    print(f"Survivor WGS84: {surv_wgs84}")
    
    estimates = load_jsonl(autopilot_path)
    
    min_dist = float('inf')
    min_horizontal_dist = float('inf')
    
    for est in estimates:
        # est: "latitude", "longitude", "relative_altitude_m" (or altitude_msl_m in gps?)
        # Let's use latitude/longitude and compute approx distance
        lat = est["latitude"]
        lon = est["longitude"]
        
        # 1 deg lat = 111320 m
        # 1 deg lon = 111320 * cos(lat) m
        d_lat_m = (lat - surv_wgs84[0]) * 111320.0
        d_lon_m = (lon - surv_wgs84[1]) * (111320.0 * np.cos(np.radians(surv_wgs84[0])))
        
        horiz_dist = np.sqrt(d_lat_m**2 + d_lon_m**2)
        if horiz_dist < min_horizontal_dist:
            min_horizontal_dist = horiz_dist
            
    print(f"Min horizontal dist: {min_horizontal_dist}")

analyze_run("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28")
analyze_run("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke")
