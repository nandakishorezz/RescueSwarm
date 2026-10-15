import json

def read_autopilot(run_dir):
    print(f"--- {run_dir} ---")
    estimates = []
    with open(f"{run_dir}/autopilot_estimate.jsonl", 'r') as f:
        for line in f:
            estimates.append(json.loads(line))
            
    print(f"Spawn ENU: {estimates[0].get('position_enu_m', 'Not in this file')}")
    print(f"First timestamp: {estimates[0].get('ap_sim_source_time_ns', 0) / 1e9}")
    
    # Check pos at ~84s
    for e in estimates:
        t = e.get("ap_sim_source_time_ns", 0) / 1e9
        if t >= 84.0 and t < 84.2:
            lat = e["latitude"]
            lon = e["longitude"]
            print(f"At 84s: lat={lat}, lon={lon}, alt={e['relative_altitude_m']}")
            
    for e in estimates:
        t = e.get("ap_sim_source_time_ns", 0) / 1e9
        if t >= 87.0 and t < 87.2:
            lat = e["latitude"]
            lon = e["longitude"]
            print(f"At 87s: lat={lat}, lon={lon}, alt={e['relative_altitude_m']}")

read_autopilot("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28")
read_autopilot("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke")
