import json

# Check altitude progression in retry7 ALIGNING records
with open('data/simulation_runs/phase4_alignment_qualification_1_retry7/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]

print("ALIGNING records (last 15) - altitude and error:")
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
for r in aligning[-15:]:
    pos = r.get('vehicle_ekf_position_enu_m', [0,0,0])
    cmd = r.get('guided_command', {})
    print(f"  sim_ns={r['ap_sim_source_time_ns']} alt={pos[2]:.3f} vel_down={cmd.get('velocity_down_m_s', 0.0):.3f}")
