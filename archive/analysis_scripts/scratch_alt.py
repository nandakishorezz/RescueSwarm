import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry3/autopilot_estimate.jsonl') as f:
    for line in f.readlines()[-5:]:
        d = json.loads(line)
        print(d)
