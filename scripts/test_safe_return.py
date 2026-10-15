"""Trigger safe return and monitor until both vehicles land, disarm, and stop."""

import json
import time
import urllib.request

print("[1] Requesting safe fleet return...")
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/multidrone/stop",
    data=b"{}",
    headers={"Content-Type": "application/json"},
)
resp = json.loads(urllib.request.urlopen(req).read())
print(f"[2] Initial stop response: {resp.get('state')} ({resp.get('state_reason')})")

deadline = time.time() + 240
while time.time() < deadline:
    s = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/multidrone/status").read())
    fs = s.get("state")
    v1 = s["vehicles"][0] if len(s.get("vehicles", [])) > 0 else {}
    v2 = s["vehicles"][1] if len(s.get("vehicles", [])) > 1 else {}
    
    t1 = v1.get("telemetry") or {}
    t2 = v2.get("telemetry") or {}
    
    alt1 = t1.get("relative_altitude_m")
    alt2 = t2.get("relative_altitude_m")
    mode1 = t1.get("flight_mode")
    mode2 = t2.get("flight_mode")
    armed1 = t1.get("armed")
    armed2 = t2.get("armed")
    
    print(f"[{time.strftime('%H:%M:%S')}] Fleet: {fs:9s} | "
          f"D1: {v1.get('state'):10s} mode={mode1} alt={alt1} armed={armed1} | "
          f"D2: {v2.get('state'):10s} mode={mode2} alt={alt2} armed={armed2}", flush=True)
    
    if fs in {"STOPPED", "IDLE"}:
        print("\n[3] SUCCESS: Fleet reached terminal stopped state!", flush=True)
        break
    time.sleep(3)
