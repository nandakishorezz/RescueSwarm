import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry9/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if 146.0 <= d['ap_sim_source_time_ns']/1e9 <= 147.0 and d['mission_state'] == 'ALIGNING':
            pos = d.get('vehicle_ekf_position_enu_m', [0,0,0])
            cmd = d.get('guided_command', {})
            print(f"{d['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) v_down={cmd.get('velocity_down_m_s',0):.2f}")
