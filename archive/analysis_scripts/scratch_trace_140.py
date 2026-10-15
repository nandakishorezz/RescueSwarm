import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry8/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if 140.6 <= d['ap_sim_source_time_ns']/1e9 <= 142.6 and d['mission_state'] == 'ALIGNING':
            pos = d.get('vehicle_ekf_position_enu_m', [0,0,0])
            cmd = d.get('guided_command', {})
            print(f"{d['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f}) cmd=({cmd.get('velocity_east_m_s',0):.2f},{cmd.get('velocity_north_m_s',0):.2f})")
