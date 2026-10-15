import json
import math
from pathlib import Path

run_dir = Path("data/simulation_runs/phase4_alignment_reacq_final_1_retry1")

# Get delivery gate timeline
gates = []
with open(run_dir / "DELIVERY_GATE_EVIDENCE.jsonl") as f:
    for line in f:
        gates.append(json.loads(line))

# Get alignment evidence
aligns = []
with open(run_dir / "ALIGNMENT_EVIDENCE.jsonl") as f:
    for line in f:
        aligns.append(json.loads(line))

# Get trajectory
trajs = []
with open(run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl") as f:
    for line in f:
        trajs.append(json.loads(line))

print(f"Total Trajs: {len(trajs)}, Aligns: {len(aligns)}, Gates: {len(gates)}")

if not gates:
    print("NO GATES")
    import sys; sys.exit(0)

final_time = gates[-1]["ap_sim_source_time_ns"]
start_time = final_time - 20 * 1e9

# Process trajs
traj_slice = [t for t in trajs if t["ap_sim_source_time_ns"] >= start_time]

if traj_slice:
    print("\n--- FINAL 20s TRAJECTORY ---")
    for t in traj_slice:
        dt = (final_time - t["ap_sim_source_time_ns"]) / 1e9
        state = t.get("mission_state", "?")
        err = t.get("alignment_error_m", "None")
        cmd = t.get("guided_command", {})
        print(f"t={t['ap_sim_source_time_ns']} (T-{dt:.2f}s) | State={state} | Err={err} | Cmd={cmd} | BBoxConf={t.get('bbox_confidence')}")

if gates:
    print("\n--- FINAL 20s GATES ---")
    gate_slice = [g for g in gates if g["ap_sim_source_time_ns"] >= start_time]
    for g in gate_slice:
        dt = (final_time - g["ap_sim_source_time_ns"]) / 1e9
        fresh = g.get("conditions", {}).get("track_fresh_s", {}).get("value", "?")
        err = g.get("conditions", {}).get("alignment_error_m", {}).get("value", "?")
        print(f"t={g['ap_sim_source_time_ns']} (T-{dt:.2f}s) | Freshness={fresh}s | Err={err}m")

min_err = 9999
min_err_time = None
for t in trajs:
    if "alignment_error_m" in t and t["alignment_error_m"] is not None:
        if t["alignment_error_m"] < min_err:
            min_err = t["alignment_error_m"]
            min_err_time = t["ap_sim_source_time_ns"]

print(f"\nMin err {min_err} at {min_err_time}")
if min_err_time:
    err_at_10s = None
    err_at_5s = None
    t10 = min_err_time - 10 * 1e9
    t5 = min_err_time - 5 * 1e9
    
    # closest matching times
    def get_closest(target_t):
        best_e = None
        min_dt = 1e12
        for tx in trajs:
            if "alignment_error_m" in tx and tx["alignment_error_m"] is not None:
                dt = abs(tx["ap_sim_source_time_ns"] - target_t)
                if dt < min_dt:
                    min_dt = dt
                    best_e = tx["alignment_error_m"]
        return best_e
    print(f"Err 10s before minimum: {get_closest(t10)}")
    print(f"Err 5s before minimum: {get_closest(t5)}")
    print(f"Err final: {trajs[-1].get('alignment_error_m')}")
