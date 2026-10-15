import json

# Check gate status for retry7
with open('data/simulation_runs/phase4_alignment_qualification_1_retry7/DELIVERY_GATE_EVIDENCE.jsonl') as f:
    gate_records = [json.loads(line) for line in f]

passed = [r for r in gate_records if r.get('all_passed')]
print(f"Total gate records: {len(gate_records)}, all_passed: {len(passed)}")

# Most common failures
fail_counts = {}
for r in gate_records:
    if not r.get('all_passed'):
        for k, v in r.get('conditions', {}).items():
            if not v['passed']:
                fail_counts[k] = fail_counts.get(k, 0) + 1
print("Failures:", sorted(fail_counts.items(), key=lambda x: -x[1])[:6])

# Show last 5 gate records
for r in gate_records[-3:]:
    alt_cond = r['conditions'].get('delivery_altitude_error_m', {})
    align_cond = r['conditions'].get('alignment_error_m', {})
    spd_cond = r['conditions'].get('horizontal_speed_m_s', {})
    print(f"sim_ns={r['ap_sim_source_time_ns']} alt_err={alt_cond.get('value', 'N/A'):.3f} align_err={align_cond.get('value', 'N/A'):.3f} spd={spd_cond.get('value', 'N/A'):.3f} all_pass={r.get('all_passed')}")
