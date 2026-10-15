import json
count = 0
with open('data/simulation_runs/phase4_alignment_qualification_1_retry8/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if 83.0 <= d['ap_sim_source_time_ns']/1e9 <= 140.0:
            if d['mission_state'] == 'REACQUIRING':
                count += 1
                if count % 50 == 0:
                    pos = d.get('vehicle_ekf_position_enu_m', [0,0,0])
                    print(f"{d['ap_sim_source_time_ns']/1e9:.1f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f})")
