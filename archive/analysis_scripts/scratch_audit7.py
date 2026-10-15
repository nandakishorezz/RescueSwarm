import json

# Deep audit of retry7: when did REACQUIRING start and what's the breakdown?
with open('data/simulation_runs/phase4_alignment_qualification_1_retry7/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]

state = None
for i, d in enumerate(records):
    if d['mission_state'] != state:
        state = d['mission_state']
        pos = d.get('vehicle_ekf_position_enu_m', [0, 0, 0])
        cmd = d.get('guided_command', {})
        print(f"{d['ap_sim_source_time_ns']/1e9:.1f}s {state}  pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f})")

print()
# Check alignment_error progression in the last ALIGNING block before long REACQUIRING
print("Last ALIGNING block errors:")
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
# Find last aligning block (before 110.9s)
last_block = [r for r in aligning if r['ap_sim_source_time_ns'] > 106_000_000_000]
for r in last_block[-5:]:
    gate_data = r.get('gate', {})
    ae = gate_data.get('alignment_error_m', {})
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    cmd = r.get('guided_command', {})
    print(f"  {r['ap_sim_source_time_ns']/1e9:.2f}s pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}) align_err={ae.get('value','N/A')} vel_down={cmd.get('velocity_down_m_s',0):.3f}")
