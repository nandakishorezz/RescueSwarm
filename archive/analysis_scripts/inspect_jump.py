import json

with open("data/simulation_runs/test_e2e_19j/ALIGNMENT_EVIDENCE.jsonl") as f:
    lines = f.readlines()

d46 = json.loads(lines[45])
d47 = json.loads(lines[46])

print("Frame 46:", d46)
print("Frame 47:", d47)
