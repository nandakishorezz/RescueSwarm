import json
with open('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/runs/2026-10-07_11-45-50_ef882226/last_50.json') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    try:
        data = json.loads(line)
        v = data['vehicle']
        print(f"{i:02d}: TS={v['last_update']} Lat={v['latitude']} Lon={v['longitude']} RelAlt={v['relative_altitude_m']} AbsAlt={v['absolute_altitude_m']} GS={v.get('ground_speed_m_s', 0):.2f} VS={v.get('vertical_speed_m_s', 0):.2f} Mode={v['flight_mode']} Armed={v['armed']} LandState={data.get('landed_state')} SysStatus={data.get('system_status')}")
    except Exception as e:
        print(e)
