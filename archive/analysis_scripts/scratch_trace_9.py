import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry9/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
if aligning:
    for r in aligning[-5:]:
        pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
        cmd = r.get('guided_command', {})
        print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) cmd=({cmd.get('velocity_east_m_s',0):.2f},{cmd.get('velocity_north_m_s',0):.2f})")
