import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry12/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
    
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
if aligning:
    start_time = aligning[0]['ap_sim_source_time_ns']/1e9
    end_time = aligning[-1]['ap_sim_source_time_ns']/1e9
    print(f'ALIGNING: {start_time:.2f}s to {end_time:.2f}s (duration: {end_time - start_time:.2f}s)')
    print(f'Start pos: {aligning[0].get("vehicle_ekf_position_enu_m")}')
    print(f'End pos: {aligning[-1].get("vehicle_ekf_position_enu_m")}')
    
    for r in aligning[::10] + [aligning[-1]]:
        cmd = r.get('guided_command', {})
        pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
        print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) v_down={cmd.get('velocity_down_m_s',0):.2f}")
else:
    print('No ALIGNING state found')
