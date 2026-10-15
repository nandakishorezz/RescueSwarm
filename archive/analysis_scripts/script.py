
import json
evidence = []
with open("data/simulation_runs/phase4_alignment_qualification_1_retry16/DELIVERY_GATE_EVIDENCE.jsonl") as f:
    for line in f:
        if line.strip():
            evidence.append(json.loads(line))

for i, ev in enumerate(evidence):
    fresh = ev["conditions"]["track_fresh_s"]["value"]
    if i > 0 and evidence[i-1]["conditions"]["track_fresh_s"]["value"] > 20.0 and fresh < 1.0:
        print(f"Fresh transition at index {i} time {ev['ap_sim_source_time_ns']}")
        for j in range(i, min(i+20, len(evidence))):
            c = evidence[j]["conditions"]
            print(f"j={j} fresh={c['track_fresh_s']['value']} pos={c['alignment_error_m']['value']} spd={c['horizontal_speed_m_s']['value']} alt={c['delivery_altitude_error_m']['value']}")
        break

