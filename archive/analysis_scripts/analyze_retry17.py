"""
Full retry17 forensic analysis using aligned trajectory (ALIGNING state) data.

ALIGNMENT_EVIDENCE and TRAJECTORY(ALIGNING) have same 954 records in same loop order.
Join by index.

Controller law (kp=0.45, max_speed=1.5, kd=0.85, deadband=0.1):
  scale = min(0.45, 1.5 / err)
  pos_e = east * scale,  pos_n = north * scale
  cmd_e = pos_e - ve * 0.85
  cmd_n = pos_n - vn * 0.85
  if hypot(cmd_e, cmd_n) > 1.5: clip to 1.5

But we don't have east/north error breakdown or vehicle velocity east/north.
We do have:
  - alignment: horizontal_error_m, cmd_mag
  - traj: horizontal_speed_m_s
  
We can compute:
  pos_term_mag = min(0.45, 1.5/err) * err = min(0.45*err, 1.5)
  damping_term_mag <= 0.85 * spd  (upper bound; exact depends on direction)

Worst case: damping fully opposes position term.
Suppression = damping_term_mag / pos_term_mag * 100%
"""
import json
import math
import statistics

align_file = "data/simulation_runs/phase4_alignment_qualification_1_retry17/ALIGNMENT_EVIDENCE.jsonl"
traj_file  = "data/simulation_runs/phase4_alignment_qualification_1_retry17/TARGET_SERVICE_TRAJECTORY.jsonl"

KP = 0.45
MAX_SPEED = 1.5
KD = 0.85
DEADBAND = 0.10
RADIUS = 0.50

align = []
with open(align_file) as f:
    for line in f:
        if not line.strip(): continue
        d = json.loads(line)
        cmd_e, cmd_n = d["command_enu_mps"]
        align.append({
            "ns": d["capture_monotonic_ns"],
            "err": d["horizontal_error_m"],
            "cmd_e": cmd_e, "cmd_n": cmd_n,
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
print()

# Build joined records
records = []
start_ns = traj_align[0]["sim_ns"]
for i in range(N):
    a = align[i]
    t = traj_align[i]
    err = a["err"]
    spd = t["spd"]
    sim_ns = t["sim_ns"]
    elapsed = (sim_ns - start_ns) / 1e9

    # Position term magnitude (what controller would compute for position alone)
    if err <= DEADBAND:
        pos_term_mag = 0.0
    else:
        scale = min(KP, MAX_SPEED / err)
        pos_term_mag = scale * err  # = min(KP*err, MAX_SPEED) but since scale=min(KP, Vmax/err), pos=scale*err

    # Damping term magnitude (maximum, assuming fully opposing)
    damp_term_mag = KD * spd  # worst case

    # Actual command magnitude (recorded truth)
    cmd_mag = a["cmd_mag"]

    records.append({
        "elapsed": elapsed,
        "sim_ns": sim_ns,
        "err": err,
        "spd": spd,
        "pos_term_mag": pos_term_mag,
        "damp_term_mag": damp_term_mag,
        "cmd_mag": cmd_mag,
        "suppression_pct": (damp_term_mag / pos_term_mag * 100) if pos_term_mag > 0 else 0,
    })

# ---- Time-to-converge ----
print("=== TIME-TO-CONVERGE ===")
print(f"Start sim_ns: {start_ns}")
end_ns = traj_align[-1]["sim_ns"]
print(f"End   sim_ns: {end_ns}")
print(f"Duration: {(end_ns - start_ns)/1e9:.1f} s")
print()

checkpoints = [0, 10, 20, 30, 45, 60, 75, 90]
print(f"{'Elapsed':>8}  {'Error(m)':>10}  {'Spd(m/s)':>9}  {'Cmd(m/s)':>9}  {'PosT':>6}  {'DampT':>6}")
for cp in checkpoints:
    # find nearest record
    best = min(records, key=lambda r: abs(r["elapsed"] - cp))
    print(f"{best['elapsed']:>8.1f}  {best['err']:>10.4f}  {best['spd']:>9.4f}  {best['cmd_mag']:>9.4f}  {best['pos_term_mag']:>6.4f}  {best['damp_term_mag']:>6.4f}")
print()

min_err_rec = min(records, key=lambda r: r["err"])
min_spd_rec = min(records, key=lambda r: r["spd"])
print(f"Minimum error: {min_err_rec['err']:.4f} m at elapsed {min_err_rec['elapsed']:.1f} s  (spd={min_err_rec['spd']:.4f})")
print(f"Minimum speed: {min_spd_rec['spd']:.4f} m/s at elapsed {min_spd_rec['elapsed']:.1f} s  (err={min_spd_rec['err']:.4f})")
print()

# ---- Region analysis ----
far  = [r for r in records if r["err"] > 2.0]
mid  = [r for r in records if 0.5 < r["err"] <= 2.0]
near = [r for r in records if r["err"] <= 0.5]

def region_stats(name, lst):
    if not lst:
        print(f"{name}: 0 samples")
        return
    spds = sorted(r["spd"] for r in lst)
    cmds = sorted(r["cmd_mag"] for r in lst)
    pts  = sorted(r["pos_term_mag"] for r in lst)
    dts  = sorted(r["damp_term_mag"] for r in lst)
    supps= [r["suppression_pct"] for r in lst]
    
    med = lambda s: s[len(s)//2]
    pct_damp75  = sum(1 for r in lst if r["suppression_pct"] > 75) / len(lst) * 100
    pct_cmd_low = sum(1 for r in lst if r["cmd_mag"] < 0.2) / len(lst) * 100
    
    print(f"=== {name} ({len(lst)} samples) ===")
    print(f"  median spd:          {med(spds):.4f} m/s")
    print(f"  median pos_term:     {med(pts):.4f} m/s")
    print(f"  median damp_term:    {med(dts):.4f} m/s  (KD*spd upper bound)")
    print(f"  median cmd_mag:      {med(cmds):.4f} m/s")
    print(f"  min cmd_mag:         {min(cmds):.4f} m/s")
    print(f"  max cmd_mag:         {max(cmds):.4f} m/s")
    print(f"  damp>75% of pos:     {pct_damp75:.1f}%")
    print(f"  cmd < 0.2 m/s:       {pct_cmd_low:.1f}%")
    print()

region_stats("FAR  (err>2.0)", far)
region_stats("MID  (0.5<err<=2.0)", mid)
region_stats("NEAR (err<=0.5)", near)

# ---- Was it converging? ----
print("=== CONVERGENCE PATTERN ===")
errs = [r["err"] for r in records]
first_30 = [r for r in records if r["elapsed"] <= 30]
last_30  = [r for r in records if r["elapsed"] >= ((end_ns-start_ns)/1e9 - 30)]
if first_30:
    print(f"Error reduction first 30s: {first_30[0]['err']:.4f} -> {first_30[-1]['err']:.4f}  (delta={first_30[0]['err']-first_30[-1]['err']:.4f})")
if last_30:
    print(f"Error reduction last  30s: {last_30[0]['err']:.4f} -> {last_30[-1]['err']:.4f}  (delta={last_30[0]['err']-last_30[-1]['err']:.4f})")

# Count oscillation: sign changes in error derivative
diffs = [errs[i+1]-errs[i] for i in range(len(errs)-1)]
sign_changes = sum(1 for i in range(len(diffs)-1) if diffs[i]*diffs[i+1] < 0)
print(f"Error sign changes (oscillation indicator): {sign_changes}")
print()

# ---- Representative cmd vs spd ----
print("=== COMMAND vs ACTUAL SPEED (representative samples) ===")
print(f"{'Region':<6} {'elapsed':>8}  {'err':>7}  {'cmd':>7}  {'spd':>7}  {'ratio(cmd/spd)':>14}")
for region_name, region_lst in [("FAR", far), ("MID", mid), ("NEAR", near)]:
    if not region_lst: continue
    mid_idx = len(region_lst)//2
    r = region_lst[mid_idx]
    ratio = r["cmd_mag"]/r["spd"] if r["spd"]>0 else float('inf')
    print(f"{region_name:<6} {r['elapsed']:>8.1f}  {r['err']:>7.4f}  {r['cmd_mag']:>7.4f}  {r['spd']:>7.4f}  {ratio:>14.3f}")
print()

# ---- Gate entries ----
pos_pass_samples = [r for r in records if r["err"] <= RADIUS]
spd_pass_samples = [r for r in records if r["spd"] <= 0.5]
both_pass        = [r for r in records if r["err"] <= RADIUS and r["spd"] <= 0.5]
print(f"Samples with err<=0.5: {len(pos_pass_samples)}")
print(f"Samples with spd<=0.5: {len(spd_pass_samples)}")
print(f"Samples with BOTH:     {len(both_pass)}")
