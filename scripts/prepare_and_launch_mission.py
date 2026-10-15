"""Prepare and launch the mission in the web application with:
- 3 survivors
- Expanded search area (2951 m²)
- Faster flight speed (search 7.0 m/s, transit 8.0 m/s)
- Live SITL execution
- Browser navigation to the active running mission
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from pathlib import Path
import websockets

BASE_URL = "http://127.0.0.1:8000"
CDP_BASE = "http://127.0.0.1:9222"

def post_json(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}") as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("1. Creating mission...")
    mission = post_json("/api/missions", {"name": "Autonomous Delivery Acceptance Run"})
    mission_id = mission["id"]
    print(f"Created mission ID: {mission_id}")

    print("2. Setting flight parameters (28m high altitude, 3 payload slots, fast search)...")
    flight_params = {
        "search_altitude_m": 28.0,
        "search_speed_m_s": 7.0,
        "transit_speed_m_s": 8.0,
        "approach_altitude_m": 12.0,
        "approach_speed_m_s": 3.0,
        "delivery_altitude_m": 5.0,
        "delivery_stabilization_s": 1.5,
        "safe_drop_offset_m": 2.5,
        "search_swath_overlap_percent": 50.0,
        "rtl_altitude_m": 30.0,
        "rtl_speed_m_s": 7.0,
        "payload_slots": 4,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/missions/{mission_id}/flight-parameters",
        data=json.dumps(flight_params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req) as resp:
        updated_params = json.loads(resp.read().decode("utf-8"))
    print("Flight parameters updated:", updated_params)

    print("3. Uploading expanded boundary (~2951 m²)...")
    # Coordinates in WGS84
    # Polygon corners [lat, lon]
    poly_wgs = [
        [12.97295600, 77.59186796],
        [12.97329949, 77.59220899],
        [12.97290176, 77.59260531],
        [12.97263059, 77.59223664],
    ]
    # GeoJSON format is [lon, lat]
    geojson_coords = [[pt[1], pt[0]] for pt in poly_wgs]
    geojson_coords.append(geojson_coords[0]) # close loop

    boundary_geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [geojson_coords]
            }
        }]
    }

    # Upload boundary multipart
    boundary_bytes = json.dumps(boundary_geojson).encode("utf-8")
    boundary_boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = (
        f"--{boundary_boundary}\r\n"
        f'Content-Disposition: form-data; name="boundary_file"; filename="expanded_boundary.geojson"\r\n'
        f"Content-Type: application/json\r\n\r\n"
    ).encode("utf-8") + boundary_bytes + f"\r\n--{boundary_boundary}--\r\n".encode("utf-8")

    upload_req = urllib.request.Request(
        f"{BASE_URL}/api/missions/{mission_id}/boundary",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(upload_req) as resp:
        b_res = json.loads(resp.read().decode("utf-8"))
    print("Boundary attached. Area:", b_res.get("area_m2"))

    print("4. Validating mission...")
    val_req = urllib.request.Request(f"{BASE_URL}/api/missions/{mission_id}/validate", method="POST")
    with urllib.request.urlopen(val_req) as resp:
        val_res = json.loads(resp.read().decode("utf-8"))
    print("Mission validated:", val_res["state"])

    print("5. Generating coverage plan (28m search altitude, 24m effective swath)...")
    plan_req = {
        "search_altitude_m": 28.0,
        "lane_spacing_m": 12.0,
        "effective_swath_m": 24.0,
        "along_track_footprint_m": 20.0,
        "edge_margin_m": 1.0,
        "search_speed_m_s": 7.0,
        "sweep_strategy": "AUTO_OPTIMIZED",
        "fixed_sweep_angle_deg": 0.0,
        "turn_penalty_m": 5.0,
        "min_planned_coverage_percent": 99.0,
        "max_waypoints": 500,
        "flight_parameters": flight_params,
    }
    plan = post_json(f"/api/missions/{mission_id}/plan", plan_req)
    print("Coverage plan generated. Waypoints:", len(plan.get("path_points_enu", [])))

    print("6. Preparing Simulation with 3 STANDING survivors...")
    survivors = [
        {
            "id": "survivor_01",
            "survivor_id": "survivor_01",
            "latitude": 12.97297408,
            "longitude": 77.59207073,
            "up_m": 0.0,
            "yaw_deg": -90.0,
            "bodyState": "standing",
            "body_state": "standing",
            "visual_variant": "GAZEBO_ACTOR_STANDING_CC_BY_4_0",
            "occlusion_profile": "NONE",
            "enabled": True,
        },
        {
            "id": "survivor_02",
            "survivor_id": "survivor_02",
            "latitude": 12.97291984,
            "longitude": 77.59239332,
            "up_m": 0.0,
            "yaw_deg": 0.0,
            "bodyState": "standing",
            "body_state": "standing",
            "visual_variant": "GAZEBO_ACTOR_STANDING_CC_BY_4_0",
            "occlusion_profile": "NONE",
            "enabled": True,
        },
        {
            "id": "survivor_03",
            "survivor_id": "survivor_03",
            "latitude": 12.97306447,
            "longitude": 77.59223664,
            "up_m": 0.0,
            "yaw_deg": -90.0,
            "bodyState": "standing",
            "body_state": "standing",
            "visual_variant": "GAZEBO_ACTOR_STANDING_CC_BY_4_0",
            "occlusion_profile": "NONE",
            "enabled": True,
        },
    ]

    # Reference profiles
    ref_profiles = get_json("/api/simulation/profiles/reference")
    drone = ref_profiles["drone"]
    camera = ref_profiles["camera"]
    environment = ref_profiles["environment"]
    sensor_profiles = ref_profiles.get("sensor_profiles", {})
    sensor_profile = sensor_profiles.get("REFERENCE_NOMINAL", {})

    run_id = f"stage13sim_delivery_acceptance_{int(time.time() * 1000)}"
    start_pos = {"lat": 12.9728656, "lng": 77.5926237}

    mission_world = {
        "schema_version": "1.0",
        "mission_id": mission_id,
        "boundary_wgs84": poly_wgs,
        "origin_wgs84": [start_pos["lat"], start_pos["lng"], 0.0],
        "ground_elevation_m": {"value": 0.0, "unit": "m", "provenance": "SIMULATION_ONLY"},
        "world_dimensions_m": {"value": [4000.0, 4000.0, 80.0], "unit": "m", "provenance": "ENGINEERING_ESTIMATE"},
        "launch_zone_width_m": {"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
        "launch_zone_length_m": {"value": 3.6576, "unit": "m", "provenance": "REFERENCE_PRESET"},
        "terrain_mode": "FLAT",
        "lighting": "DAY",
        "environment_profile_id": environment["profile_id"],
    }

    survivor_scenario = {
        "schema_version": "1.0",
        "scenario_id": f"{run_id}_hidden",
        "survivors": survivors,
    }

    config = {
        "schema_version": "1.0",
        "config_id": f"{run_id}_config",
        "drone_profile_id": drone["profile_id"],
        "camera_profile_id": camera["camera_id"],
        "environment_profile_id": environment["profile_id"],
        "mission_world_profile_id": mission_id,
        "survivor_scenario_id": survivor_scenario["scenario_id"],
        "random_seed": int(environment.get("random_seed", 84521)),
        "execution_source": "SITL_GAZEBO",
    }

    prep_payload = {
        "run_id": run_id,
        "config": config,
        "drone": drone,
        "camera": camera,
        "environment": environment,
        "mission": mission_world,
        "survivors": survivor_scenario,
        "sensor_profile": sensor_profile,
        "sensor_seeds": {
            "sensor_seed": 31003,
            "wind_seed": int(environment.get("wind", {}).get("random_seed", 84521)),
            "scenario_seed": int(environment.get("random_seed", 84521)),
        },
        "phase4_payload_slots": 0,
        "fleet_count": 1,
    }

    print("Submitting prepareSimulation definition...")
    try:
        post_json("/api/simulation/stop", {})
    except Exception:
        pass
    time.sleep(1.0)
    prep_res = post_json("/api/simulation/prepare", prep_payload)
    print("Simulation prepared:", prep_res)

    print("7. Updating browser via CDP and starting mission execution...")
    # Scenario object for localStorage
    ls_scenario = {
        "drone": drone,
        "camera": camera,
        "environment": environment,
        "survivors": survivors,
        "sensorProfileId": "REFERENCE_NOMINAL",
        "sensorSeed": 31003,
        "startPosition": start_pos,
        "polygon": [[pt[1], pt[0]] for pt in poly_wgs],
    }

    async def update_browser_and_start():
        tabs = json.loads(urllib.request.urlopen(f"{CDP_BASE}/json").read().decode("utf-8"))
        page_tabs = [t for t in tabs if t.get("type") == "page" and "5173" in t.get("url", "")]
        if not page_tabs:
            page_tabs = [t for t in tabs if t.get("type") == "page"]
        target_tab = page_tabs[0]
        ws_url = target_tab["webSocketDebuggerUrl"]
        print(f"Connecting to browser tab: {target_tab['title']} ({ws_url})")

        async with websockets.connect(ws_url) as ws:
            # Set localStorage scenario
            ls_json = json.dumps(json.dumps(ls_scenario))
            js_set_ls = f"localStorage.setItem('rescueswarm.simulationScenario', {ls_json});"
            await ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": js_set_ls}
            }))
            await ws.recv()

            # Navigate page to new mission
            target_nav_url = f"http://127.0.0.1:5173/?mode=mission&mission={mission_id}&view=split"
            print(f"Navigating browser to: {target_nav_url}")
            await ws.send(json.dumps({
                "id": 2,
                "method": "Page.navigate",
                "params": {"url": target_nav_url}
            }))
            await ws.recv()
            await asyncio.sleep(2.0)

            # Re-set localStorage in new context just in case
            await ws.send(json.dumps({
                "id": 3,
                "method": "Runtime.evaluate",
                "params": {"expression": js_set_ls}
            }))
            await ws.recv()

    asyncio.run(update_browser_and_start())

    print("8. Executing SITL mission...")
    exec_req = urllib.request.Request(
        f"{BASE_URL}/api/engineering/sitl/missions/{mission_id}/execute",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(exec_req) as resp:
        exec_res = json.loads(resp.read().decode("utf-8"))
    print("Mission execute response:", exec_res)

    print("\nSUCCESS! Mission is actively running in the web browser.")
    print(f"URL: http://127.0.0.1:5173/?mode=mission&mission={mission_id}&view=split")
    print(f"Survivors: 3")
    print(f"Area: {b_res.get('area_m2'):.1f} m²")
    print(f"Search Speed: {flight_params['search_speed_m_s']} m/s")

if __name__ == "__main__":
    main()
