import json
with open('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/runs/2026-10-07_11-45-50_ef882226/telemetry.ndjson') as f:
    lines = f.readlines()
print('Total lines:', len(lines))
for i in [0, len(lines)-1]:
    data = json.loads(lines[i])
    v = data['vehicle']
    print(f"Line {i}: TS={v['last_update']} RelAlt={v['relative_altitude_m']} AbsAlt={v['absolute_altitude_m']}")
