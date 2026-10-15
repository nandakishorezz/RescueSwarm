import json
from pathlib import Path

run_dir = Path("data/simulation_runs/phase4_alignment_reacq_final_1_retry1")

aligns = []
with open(run_dir / "ALIGNMENT_EVIDENCE.jsonl") as f:
    for line in f:
        aligns.append(json.loads(line))

trajs = []
with open(run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl") as f:
    for line in f:
        trajs.append(json.loads(line))

gates = []
with open(run_dir / "DELIVERY_GATE_EVIDENCE.jsonl") as f:
    for line in f:
        gates.append(json.loads(line))

# Match best error from ALIGNMENT_EVIDENCE
best_err = 9999
best_align = None
for a in aligns:
    err = a.get("horizontal_error_m")
    if err is not None and err < best_err:
        best_err = err
        best_align = a

print(f"Best err from ALIGNMENT_EVIDENCE: {best_err}")

final_time = aligns[-1]["capture_monotonic_ns"]
start_time = final_time - 20 * 1e9

slice = [a for a in aligns if a["capture_monotonic_ns"] >= start_time]

print("\n--- FINAL 20s ALIGNS ---")
for a in slice[-10:]:
    dt = (final_time - a["capture_monotonic_ns"]) / 1e9
    print(f"T-{dt:.2f}s | Err={a.get('horizontal_error_m')} | Cmd={a.get('command_enu_mps')}")

print("\n--- MIN ALIGN ERROR & TREND ---")
t_min_align = best_align["capture_monotonic_ns"]
t_10s = t_min_align - 10 * 1e9
t_5s = t_min_align - 5 * 1e9

def get_closest_align(t_target):
    best = None
    min_dt = 1e12
    for a in aligns:
        dt = abs(a["capture_monotonic_ns"] - t_target)
        if dt < min_dt:
            min_dt = dt
            best = a["horizontal_error_m"]
    return best

print(f"Error 10s before minimum: {get_closest_align(t_10s)}")
print(f"Error 5s before minimum: {get_closest_align(t_5s)}")
print(f"Error final: {aligns[-1].get('horizontal_error_m')}")

print("\n--- LAST GATE ---")
print(gates[-1])

print("\n--- FINAL 5 TRAJS ---")
for t in trajs[-5:]:
    print(f"{t['ap_sim_source_time_ns']} | Err={t.get('alignment_error_m')} | Vel={t.get('horizontal_speed_m_s')} | Gate={t.get('delivery_gate')}")
