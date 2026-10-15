import json

with open("data/simulation_runs/test_e2e_19j/ALIGNMENT_EVIDENCE.jsonl") as f:
    lines = f.readlines()

for l in lines[:100]:
    d = json.loads(l)
    print(f"err: {d['horizontal_error_m']:.2f}")

