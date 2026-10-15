import json
import math

align_file = "data/simulation_runs/phase4_alignment_qualification_1_retry19/ALIGNMENT_EVIDENCE.jsonl"
traj_file  = "data/simulation_runs/phase4_alignment_qualification_1_retry19/TARGET_SERVICE_TRAJECTORY.jsonl"

align = []
with open(align_file) as f:
    for line in f:
        if not line.strip(): continue
        d = json.loads(line)
        cmd_e, cmd_n = d["command_enu_mps"]
        align.append({
            "ns": d["capture_monotonic_ns"],
            "err": d["horizontal_error_m"],
            "cmd_mag": math.hypot(cmd_e, cmd_n),
        })

traj_align = []
with open(traj_file) as f:
    for line in f:
        if not line.strip(): continue
        d = json.loads(line)
        if d.get("mission_state") == "ALIGNING":
            traj_align.append({
                "sim_ns": d["ap_sim_source_time_ns"],
                "spd": d["horizontal_speed_m_s"],
            })

N = min(len(align), len(traj_align))
print(f"Joined {N} ALIGNING samples (align={len(align)}, traj={len(traj_align)})")

if N == 0:
    print("No ALIGNING samples - checking all states")
    states = set()
    with open(traj_file) as f:
        for line in f:
            if not line.strip(): continue
            d = json.loads(line)
            states.add(d.get("mission_state"))
    print("States:", states)
    exit()

records = []
start_ns = traj_align[0]["sim_ns"]
end_ns = traj_align[-1]["sim_ns"]
duration = (end_ns - start_ns) / 1e9
print(f"Duration: {duration:.1f}s")

for i in range(N):
    a = align[i]; t = traj_align[i]
    err = a["err"]; spd = t["spd"]
    elapsed = (t["sim_ns"] - start_ns) / 1e9
    pos_term = min(0.45, 1.5/err)*err if err > 0.10 else 0.0
    kd_eff = 0.85 * max(0.0, 1.0 - err / 2.0)
    damp_term = kd_eff * spd
    records.append({"elapsed": elapsed, "err": err, "spd": spd, "cmd_mag": a["cmd_mag"],
                    "pos_term": pos_term, "damp_term": damp_term})

# Time-to-converge
print("\n=== TIME-TO-CONVERGE ===")
print(f"{'Elapsed':>8}  {'Error(m)':>10}  {'Spd(m/s)':>9}  {'Cmd(m/s)':>9}  {'PosT':>6}  {'DampT':>6}")
for cp in [0, 10, 20, 30, 45, 60, 75, 90]:
    best = min(records, key=lambda r: abs(r["elapsed"] - cp))
    print(f"{best['elapsed']:>8.1f}  {best['err']:>10.4f}  {best['spd']:>9.4f}  {best['cmd_mag']:>9.4f}  {best['pos_term']:>6.4f}  {best['damp_term']:>6.4f}")

min_err_rec = min(records, key=lambda r: r["err"])
min_spd_rec = min(records, key=lambda r: r["spd"])
max_spd_rec = max(records, key=lambda r: r["spd"])
med_spd = sorted(r["spd"] for r in records)[len(records)//2]
print(f"\nMinimum error: {min_err_rec['err']:.4f} m at t={min_err_rec['elapsed']:.1f}s  spd={min_err_rec['spd']:.4f}")
print(f"Speed at min error: {min_err_rec['spd']:.4f} m/s")
print(f"Minimum speed: {min_spd_rec['spd']:.4f} m/s at t={min_spd_rec['elapsed']:.1f}s  err={min_spd_rec['err']:.4f}")
print(f"Maximum speed: {max_spd_rec['spd']:.4f} m/s")
print(f"Median speed: {med_spd:.4f} m/s")

# Region analysis
far  = [r for r in records if r["err"] > 2.0]
mid  = [r for r in records if 0.5 < r["err"] <= 2.0]
near = [r for r in records if r["err"] <= 0.5]

for name, lst in [("FAR", far), ("MID", mid), ("NEAR", near)]:
    if not lst:
        print(f"\n{name}: 0 samples")
        continue
    spds = sorted(r["spd"] for r in lst)
    cmds = sorted(r["cmd_mag"] for r in lst)
    med = lambda s: s[len(s)//2]
    print(f"\n{name} ({len(lst)} samples): med_spd={med(spds):.4f}  med_cmd={med(cmds):.4f}  min_cmd={min(cmds):.4f}  max_cmd={max(cmds):.4f}")

# Gate overlap
RADIUS = 0.5; SPD_GATE = 0.5
pos_pass = [r for r in records if r["err"] <= RADIUS]
spd_pass = [r for r in records if r["spd"] <= SPD_GATE]
both = [r for r in records if r["err"] <= RADIUS and r["spd"] <= SPD_GATE]
print(f"\nGate samples: pos_pass={len(pos_pass)}  spd_pass={len(spd_pass)}  BOTH={len(both)}")

# Oscillation
errs = [r["err"] for r in records]
diffs = [errs[i+1]-errs[i] for i in range(len(errs)-1)]
sign_changes = sum(1 for i in range(len(diffs)-1) if diffs[i]*diffs[i+1] < 0)
print(f"Error sign changes: {sign_changes}")

# Position/speed gate entry/exit duration windows
in_pos = False; pos_windows = []; pos_start = None
in_spd = False; spd_windows = []; spd_start = None
in_both = False; both_windows = []; both_start = None
for r in records:
    p = r["err"] <= RADIUS; s = r["spd"] <= SPD_GATE; b = p and s
    if p and not in_pos:  in_pos=True; pos_start=r["elapsed"]
    if not p and in_pos:  in_pos=False; pos_windows.append(r["elapsed"]-pos_start)
    if s and not in_spd:  in_spd=True; spd_start=r["elapsed"]
    if not s and in_spd:  in_spd=False; spd_windows.append(r["elapsed"]-spd_start)
    if b and not in_both: in_both=True; both_start=r["elapsed"]
    if not b and in_both: in_both=False; both_windows.append(r["elapsed"]-both_start)
if in_pos: pos_windows.append(records[-1]["elapsed"]-pos_start)
if in_spd: spd_windows.append(records[-1]["elapsed"]-spd_start)
if in_both: both_windows.append(records[-1]["elapsed"]-both_start)
print(f"Longest pos window:  {max(pos_windows) if pos_windows else 0:.2f}s  (entries={len(pos_windows)})")
print(f"Longest spd window:  {max(spd_windows) if spd_windows else 0:.2f}s")
print(f"Longest both window: {max(both_windows) if both_windows else 0:.2f}s")
