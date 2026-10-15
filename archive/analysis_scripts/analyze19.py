import json

audit_file = 'data/audit_hidden_01/raw_output_index.jsonl'
lines = [json.loads(line) for line in open(audit_file)]

for d in lines[-20:]:
    print(f"Time: {d['capture_timestamp_sim_ns']} MaxConf: {d['raw_person_max_confidence']:.2f}")

