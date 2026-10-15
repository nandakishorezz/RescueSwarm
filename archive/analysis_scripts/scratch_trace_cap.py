import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry9/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if 146.0 <= d['ap_sim_source_time_ns']/1e9 <= 147.0 and d['mission_state'] == 'ALIGNING':
            pos = d.get('vehicle_ekf_position_enu_m', [0,0,0])
            cmd = d.get('guided_command', {})
            cap = d.get('capture_monotonic_ns', 0)
            print(f"{d['ap_sim_source_time_ns']/1e9:.2f}s cap={cap/1e9:.2f}s")
