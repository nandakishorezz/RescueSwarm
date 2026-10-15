import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry5/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    for line in f.readlines()[-5:]:
        d = json.loads(line)
        print(f"{d.get('mission_state', '')} {d.get('ap_sim_source_time_ns', '')}")
