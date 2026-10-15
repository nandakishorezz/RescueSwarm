import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry9/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
for r in records[-5:]:
    print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s {r['mission_state']}")
