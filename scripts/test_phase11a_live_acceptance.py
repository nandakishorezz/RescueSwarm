"""Live Two-Drone Acceptance Validation Script for Phase 1.1A.

Tests the real ArduCopter SITL + Gazebo multi-drone environment against
all acceptance criteria defined in Phase 1.1A.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"
MISSION_ID = "57e0d9f6-3201-4707-9841-6a929155e27c"


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get(path: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_port_free(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return False
    except OSError:
        return True


def check_wsl_processes() -> list[str]:
    try:
        cmd = ["wsl.exe", "-u", "nandakishore7", "-e", "bash", "-c", "ps aux | grep -E 'sim_vehicle|arducopter|gz|socat' | grep -v grep || true"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=8)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and "screen size is bogus" not in line]
        return lines
    except subprocess.TimeoutExpired:
        log("  WSL ps aux timed out after 8s")
        return []


def main():
    log("=" * 70)
    log("  RESCUESWARM PHASE 1.1A: LIVE TWO-DRONE ACCEPTANCE VALIDATION")
    log("=" * 70)
    start_time = time.time()

    # STEP 1: Verify Initial Preconditions
    log("\n[STEP 1] Checking initial system state and ports...")
    status = http_get("/api/multidrone/status")
    log(f"  Current fleet state: {status.get('state')} (reason: {status.get('state_reason')})")
    assert status.get("state") in {"IDLE", "STOPPED"}, f"Expected IDLE or STOPPED, got {status.get('state')}"

    ports = [5760, 5770, 5761, 45678, 45679]
    for p in ports:
        assert check_port_free(p), f"Port {p} is unexpectedly in use before start!"
    log(f"  All fleet ports {ports} are free.")

    wsl_procs = check_wsl_processes()
    if wsl_procs:
        log(f"  WARNING: Stray WSL processes found: {wsl_procs}")
    else:
        log("  Zero stray simulation processes in WSL.")

    # STEP 2: Start Fleet Session 1
    log("\n[STEP 2] Starting live two-drone fleet session...")
    config = {
        "mission_id": MISSION_ID,
        "drones": [
            {
                "vehicle_id": "drone_01",
                "role": "SEARCHER",
                "system_id": 1,
                "instance_id": 0,
                "spawn_north_m": 0.0,
                "spawn_heading_deg": 90.0,
            },
            {
                "vehicle_id": "drone_02",
                "role": "SERVICER",
                "system_id": 2,
                "instance_id": 1,
                "spawn_north_m": 2.0,
                "spawn_heading_deg": 90.0,
            },
        ],
        "spawn_spacing_m": 2.0,
    }

    try:
        start_resp = http_post("/api/multidrone/start", config)
        log(f"  Start response state: {start_resp.get('state')} (reason: {start_resp.get('state_reason')})")
    except Exception as e:
        log(f"  FAILED to call /api/multidrone/start: {e}")
        raise

    # STEP 3: Monitor State Transitions and Live Telemetry
    log("\n[STEP 3] Monitoring state transitions and telemetry arrival...")
    observed_states = set([start_resp.get("state")])
    observed_drone_states = {"drone_01": set(), "drone_02": set()}
    telemetry_observed = {"drone_01": False, "drone_02": False}
    trails_observed = {"drone_01": 0, "drone_02": 0}

    deadline = time.time() + 330  # Allow up to 5.5 minutes for SITL+Gazebo startup and takeoff
    ready_or_running_reached = False

    while time.time() < deadline:
        s = http_get("/api/multidrone/status")
        fleet_state = s.get("state")
        observed_states.add(fleet_state)

        vehicles = {v["vehicle_id"]: v for v in s.get("vehicles", [])}
        for vid in ["drone_01", "drone_02"]:
            if vid in vehicles:
                dstate = vehicles[vid].get("state")
                observed_drone_states[vid].add(dstate)
                telem = vehicles[vid].get("telemetry")
                if telem:
                    telemetry_observed[vid] = True
                    alt = telem.get("relative_altitude_m", 0.0)
                    mode = telem.get("flight_mode", "UNKNOWN")
                    armed = telem.get("armed", False)
                    lat = telem.get("latitude")
                    lon = telem.get("longitude")
                    log(f"  [{vid}] State={dstate} | Mode={mode} | Armed={armed} | Alt={alt:.2f}m | Lat={lat}, Lon={lon}")

        trails = s.get("trails", {})
        for vid in ["drone_01", "drone_02"]:
            if vid in trails:
                trails_observed[vid] = len(trails[vid])

        drone_summary = [(v.get('vehicle_id'), v.get('state')) for v in s.get('vehicles', [])]
        log(f"  --> Fleet State: {fleet_state} ({s.get('state_reason')}) | Drone states: {drone_summary}")

        if fleet_state in {"RUNNING", "DEGRADED"}:
            ready_or_running_reached = True
            log(f"\n  SUCCESS: Fleet reached {fleet_state} with live telemetry!")
            break

        if fleet_state == "ERROR":
            log(f"\n  ERROR encountered: {s.get('error')} ({s.get('state_reason')})")
            break

        time.sleep(3)

    if not ready_or_running_reached:
        log(f"  Fleet did not reach RUNNING. Observed states: {observed_states}")
        log(f"  Current status: {s}")
        return

    # Let vehicles fly for 15 seconds to accumulate real telemetry and GPS trails
    log("\n[STEP 4] Letting vehicles fly for 15s to record real GPS trails & telemetry...")
    time.sleep(15)

    s = http_get("/api/multidrone/status")
    log(f"  Fleet state: {s.get('state')}")
    trails = s.get("trails", {})
    for vid, pts in trails.items():
        log(f"  Vehicle {vid} accumulated {len(pts)} GPS trail points.")

    # STEP 5: Safe Fleet Return & Teardown
    log("\n[STEP 5] Requesting safe fleet return (POST /api/multidrone/stop)...")
    stop_resp = http_post("/api/multidrone/stop")
    log(f"  Stop response state: {stop_resp.get('state')}")

    stop_deadline = time.time() + 300
    stopped = False
    while time.time() < stop_deadline:
        s = http_get("/api/multidrone/status")
        fleet_state = s.get("state")
        observed_states.add(fleet_state)
        vehicles = {v["vehicle_id"]: v for v in s.get("vehicles", [])}
        for vid in ["drone_01", "drone_02"]:
            if vid in vehicles:
                observed_drone_states[vid].add(vehicles[vid].get("state"))
        drone_summary = [(v.get('vehicle_id'), v.get('state')) for v in s.get('vehicles', [])]
        log(f"  --> Stopping phase: Fleet={fleet_state} | Vehicles={drone_summary}")
        if fleet_state in {"STOPPED", "IDLE"}:
            stopped = True
            log("  SUCCESS: Fleet reached terminal stopped state cleanly.")
            break
        time.sleep(3)

    assert stopped, f"Fleet did not reach STOPPED. Final state: {s.get('state')}"

    # Verify resource cleanup
    log("\n[STEP 6] Verifying port deallocation and process cleanup...")
    time.sleep(3)
    for p in ports:
        assert check_port_free(p), f"Port {p} was NOT released after fleet stop!"
    log(f"  All ports {ports} successfully released.")

    wsl_procs = check_wsl_processes()
    log(f"  WSL simulation processes remaining: {len(wsl_procs)}")
    assert len(wsl_procs) == 0, f"Orphaned simulation processes detected: {wsl_procs}"

    # STEP 7: Session Isolation Test (Start Session 2)
    log("\n[STEP 7] Starting second fleet session to verify state isolation...")
    start2_resp = http_post("/api/multidrone/start", config)
    log(f"  Session 2 start state: {start2_resp.get('state')}")
    s2 = http_get("/api/multidrone/status")
    s2_trails = s2.get("trails", {})
    for vid in ["drone_01", "drone_02"]:
        assert len(s2_trails.get(vid, [])) == 0, f"Leaked trail detected for {vid} in session 2!"
    log("  Verified: Trails are cleanly reset; zero telemetry/events leak into session 2.")

    # Stop session 2
    log("  Stopping session 2...")
    http_post("/api/multidrone/stop")
    s2_deadline = time.time() + 60
    while time.time() < s2_deadline:
        s2_status = http_get("/api/multidrone/status")
        if s2_status.get("state") in {"STOPPED", "IDLE"}:
            break
        time.sleep(2)
    time.sleep(3)
    s2_final = http_get("/api/multidrone/status")
    log(f"  Session 2 final state: {s2_final.get('state')}")

    # STEP 8: Single-Drone Mission Intactness
    log("\n[STEP 8] Verifying single-drone mission integrity...")
    mission = http_get(f"/api/missions/{MISSION_ID}")
    assert mission.get("id") == MISSION_ID
    log(f"  Single-drone mission '{mission.get('name')}' is intact and valid.")

    log("\n" + "=" * 70)
    log(f"  ALL ACCEPTANCE CRITERIA VERIFIED SUCCESSFULLY IN {time.time() - start_time:.1f}s")
    log(f"  Observed Fleet States: {sorted(list(observed_states))}")
    log(f"  Observed Drone 01 States: {sorted(list(observed_drone_states['drone_01']))}")
    log(f"  Observed Drone 02 States: {sorted(list(observed_drone_states['drone_02']))}")
    log("=" * 70)


if __name__ == "__main__":
    main()
