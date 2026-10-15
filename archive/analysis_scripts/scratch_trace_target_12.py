import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry12/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
    
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
for r in aligning[::10] + [aligning[-1]]:
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    v_pos = r.get('visual_target_enu_m')
    print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f}) target={v_pos}")
