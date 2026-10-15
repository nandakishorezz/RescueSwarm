import json

with open("data/simulation_runs/phase4_alignment_qualification_1_retry18/autopilot_estimate.jsonl") as f:
    rows = [json.loads(l) for l in f if l.strip()]

print(f"Total rows: {len(rows)}")
print(f"All keys: {list(rows[0].keys())}")
print(f"First row: {json.dumps(rows[0], indent=2)[:500]}")

# Time field
t_key = "ap_sim_source_time_ns"
print(f"\nFirst time: {rows[0][t_key]/1e9:.2f}s")
print(f"Last  time: {rows[-1][t_key]/1e9:.2f}s")

# Mode transitions from events
with open("data/simulation_runs/phase4_alignment_qualification_1_retry18/events.jsonl") as f:
    events = [json.loads(l) for l in f if l.strip()]
print("\nEvents:")
for e in events:
    print(f"  {e}")

# Vision events - look for RTL trigger
with open("data/simulation_runs/phase4_alignment_qualification_1_retry18/vision_events.jsonl") as f:
    vevents = [json.loads(l) for l in f if l.strip()]

print(f"\nTotal vision events: {len(vevents)}")
# Find RTL/mission complete related events
rtl_events = [e for e in vevents if any(kw in str(e).upper() for kw in ["RTL", "MISSION", "COMPLETE", "WAYPOINT", "NAV"])]
print(f"RTL/mission related vision events: {len(rtl_events)}")
for e in rtl_events[:5]:
    print(f"  {e}")
