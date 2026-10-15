import json
with open('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/runs/2026-10-07_11-45-50_ef882226/telemetry.ndjson') as f:
    lines = f.readlines()
for i in range(0, 500, 10):
    data = json.loads(lines[i])
    v = data['vehicle']
    if v['relative_altitude_m'] != 0:
        print(f"{i:04d}: TS={v['last_update']} RelAlt={v['relative_altitude_m']} AbsAlt={v.get('absolute_altitude_m')} Mode={v['flight_mode']} Armed={v['armed']}")
