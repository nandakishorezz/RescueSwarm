# Stage 7S — autonomous rescue orchestration in ArduCopter SITL

Stage 7S joins the existing search planner, Stage 5 synthetic-camera geolocation, ArduCopter AUTO mission execution, a guarded GUIDED diversion, synthetic close-range reacquisition, and a simulated payload. It never connects this command path to physical hardware.

Hardware Gate H1 remains **PENDING**. No Stage 7S result verifies the Raspberry Pi 5, OV9281, Pixhawk, M8N, physical payload, or physical flight safety.

## Gold workflow

1. Generate and verify a coverage plan.
2. Launch official ArduCopter SITL locally.
3. Upload and download-verify the integer MAVLink mission.
4. Arm SITL and enter AUTO.
5. Project a private synthetic survivor through the Stage 5 camera model.
6. Inverse-geolocate multiple observations and confirm one target.
7. Persist the last-reached search checkpoint.
8. Confirm GUIDED through an actual HEARTBEAT.
9. Follow a path that remains inside the operational polygon.
10. Reacquire and align using real SITL position changes.
11. Release and verify one simulated payload.
12. Set the conservative replay sequence, confirm AUTO and MISSION_CURRENT, then finish coverage.
13. Observe RTL, landing, and disarm.

Run from PowerShell:

```powershell
$env:ARDUPILOT_HOME='/home/nandakishore7/ardupilot'
py -3.13 scripts/run_stage7s_sitl_rescue.py
```

The output lives under `data/rescue_runs/`. The root `STAGE_7S_REPORT.md` is copied from the latest successful run.

For the search-continuity control, run the exact same mission without a rescue
diversion after the gold run:

```powershell
py -3.13 scripts/run_stage7s_coverage_baseline.py
```

This persists an independent uninterrupted SITL execution and compares its
search-only coverage, missed area, overlap, distance, and duration with the
rescue-interrupted execution. GUIDED and RTL trail are excluded from the search
coverage calculation.

## Safety boundary

`SITLSafetyGuard` requires `environment=sitl`, `command_authority=simulation_only`, and an explicit loopback IP. Serial devices, Windows COM ports, hostnames, LAN addresses, and public endpoints fail before a connection opens. The payload module has no flight-controller or GPIO dependency. Competition mode returns HTTP 403 for every engineering SITL rescue endpoint.

## Data classification

- Vehicle flight and modes: real official ArduCopter SITL telemetry.
- Search/approach movement: real SITL dynamics and position.
- Survivor image/detection: synthetic Stage 5 projection.
- Reacquisition: synthetic relative observation derived from actual SITL position.
- Payload: simulated logical state only.
- H1 physical evidence: not tested.
