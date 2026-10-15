import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry6/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    state = None
    for line in f:
        d = json.loads(line)
        if d['mission_state'] != state:
            state = d['mission_state']
            print(f"{d['ap_sim_source_time_ns']} {state}")
