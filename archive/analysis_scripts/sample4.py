import json
with open('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/runs/2026-10-07_11-45-50_ef882226/telemetry.ndjson') as f:
    lines = f.readlines()
for line in lines[-30:]:
    data = json.loads(line)
    v = data['vehicle']
    print(f"TS={v['last_update']} Lat={v['latitude']} Lon={v['longitude']} RelAlt={v['relative_altitude_m']} AbsAlt={v.get('absolute_altitude_m')} GS={v.get('ground_speed_m_s')} VS={v.get('vertical_speed_m_s')} Mode={v['flight_mode']} Armed={v['armed']} LandState={data.get('landed_state')} SysStatus={data.get('system_status')}")
