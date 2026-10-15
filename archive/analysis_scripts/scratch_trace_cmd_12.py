import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry12/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
    
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
for r in aligning[::10] + [aligning[-1]]:
    cmd = r.get('guided_command', {})
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    ve = cmd.get('velocity_east_m_s', 0)
    vn = cmd.get('velocity_north_m_s', 0)
    print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) cmd=({ve:.2f},{vn:.2f})")
