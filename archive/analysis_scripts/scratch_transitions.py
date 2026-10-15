import json

# Look at the last few ALIGNING records before each REACQUIRING, check alignment error and stale obs
with open('data/simulation_runs/phase4_alignment_qualification_1_retry5/TARGET_SERVICE_TRAJECTORY.jsonl') as f:
    records = [json.loads(line) for line in f]

# Find all state transitions
state = None
transition_indices = []
for i, d in enumerate(records):
    if d['mission_state'] != state:
        transition_indices.append((i, d['mission_state']))
        state = d['mission_state']

# For each ALIGNING->REACQUIRING transition, show the last 3 ALIGNING records
for idx, (i, new_state) in enumerate(transition_indices):
    if new_state == 'REACQUIRING' and idx > 0 and transition_indices[idx-1][1] == 'ALIGNING':
        prev_start = transition_indices[idx-1][0]
        print(f"Transition at index {i} (sim_ns={records[i]['ap_sim_source_time_ns']}): ALIGNING->REACQUIRING")
        # Show last 3 ALIGNING records
        for j in range(max(prev_start, i-3), i):
            d = records[j]
            pos = d.get('vehicle_ekf_position_enu_m', [0,0,0])
            gate = d.get('gate', {})
            cmd = d.get('guided_command', {})
            print(f"  [{j}] sim_ns={d['ap_sim_source_time_ns']} pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f})")
