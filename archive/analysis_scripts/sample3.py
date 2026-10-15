import json
with open('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/runs/2026-10-07_11-45-50_ef882226/telemetry.ndjson') as f:
    lines = f.readlines()
for line in lines:
    data = json.loads(line)
    v = data['vehicle']
    ts = v['last_update']
    if 'T11:45:52.7' <= ts[10:] <= 'T11:45:53.1':
        print(f"TS={v['last_update']} RelAlt={v['relative_altitude_m']} AbsAlt={v.get('absolute_altitude_m')} Mode={v['flight_mode']} Armed={v['armed']}")
