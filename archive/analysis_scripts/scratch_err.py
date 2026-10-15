import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry8/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
aligning = [r for r in records if r['mission_state'] == 'ALIGNING' and r['ap_sim_source_time_ns'] < 83700000000]
for r in aligning[-5:]:
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    gate_data = r.get('gate', {})
    ae = gate_data.get('alignment_error_m', {})
    print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) align_err={ae.get('value','N/A')}")
