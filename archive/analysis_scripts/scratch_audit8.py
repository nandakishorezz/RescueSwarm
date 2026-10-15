import json

with open('data/simulation_runs/phase4_alignment_qualification_1_retry8/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]

state = None
for i, d in enumerate(records):
    if d['mission_state'] != state:
        state = d['mission_state']
        pos = d.get('vehicle_ekf_position_enu_m', [0, 0, 0])
        print(f"{d['ap_sim_source_time_ns']/1e9:.1f}s {state}  pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f})")

print('Last ALIGNING block errors:')
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
for r in aligning[-5:]:
    gate_data = r.get('gate', {})
    ae = gate_data.get('alignment_error_m', {})
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    cmd = r.get('guided_command', {})
    print(f"  {r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) align_err={ae.get('value','N/A')} vel_down={cmd.get('velocity_down_m_s',0):.3f}")
