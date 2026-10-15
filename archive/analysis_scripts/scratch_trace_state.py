import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry9/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f:
        d = json.loads(line)
        if 146.5 <= d['ap_sim_source_time_ns']/1e9 <= 148.0:
            print(f"{d['ap_sim_source_time_ns']/1e9:.2f}s {d['mission_state']}")
