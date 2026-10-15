import json
with open('data/simulation_runs/phase4_alignment_qualification_1_retry12/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]
    
aligning = [r for r in records if r['mission_state'] == 'ALIGNING']
for r in aligning[::10] + [aligning[-1]]:
    cmd = r.get('guided_command', {})
    print(f"{r['ap_sim_source_time_ns']/1e9:.2f}s err_m={cmd.get('horizontal_error_m')}")
