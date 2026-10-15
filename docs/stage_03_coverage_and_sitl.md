# Stage 3 — coverage planning and ArduCopter SITL

Stage 3 adds the search-flight foundation without touching physical hardware. A validated Stage 2 boundary becomes an optimized local-metre plan, then a simulation-only adapter uploads and verifies an integer ArduCopter mission. ArduCopter owns every AUTO waypoint after start; the GCS does not stream position commands.

In Setup Mode, validate GeoJSON or KML, inspect engineering defaults, select automatic or fixed sweep, and generate the route. The local map distinguishes boundary, safe inset, green search passes, dashed transit connectors, waypoints, mission origin, and amber actual trail.

Engineering Mode can start official SITL using an explicit site MSL altitude or connect to an existing loopback instance. `RUN SITL SEARCH MISSION` performs readiness, upload, download verification, arm, AUTO, search, explicit RTL, landing, and disarm monitoring. `ABORT SITL -> RTL` remains guarded and disappears in competition mode.

Mission Mode receives real SITL heartbeat, position, GPS, attitude, battery, mission progress, landed state, and status text through the existing WebSocket. Stale MAVLink causes link loss; positions are never invented. Useful-rate telemetry reaches the browser, downsampled state is persisted, and normalized evidence is written to files.

Actual coverage buffers only trail segments observed while a search mission item is active. Takeoff and RTL are excluded. Review Mode reports source, fingerprint, planned/actual distance and coverage, item progress, RTL/land/disarm evidence, and planned versus actual paths.

Each live attempt writes `data/runs/<timestamp>_<mission>/`: mission input, coverage JSON/GeoJSON, MAVLink mission, verification, telemetry/events NDJSON, actual trail, execution summary, and report. `tools/generate_stage3_report.py` says `NOT_TESTED` when live evidence is absent.

`SimulatedVehicleSource` remains the Stage 2 UI simulator. `SITLVehicleSource` uses actual ArduCopter MAVLink. `REAL` is reserved and unimplemented. Coverage values in `config/ground.yaml` are SITL test defaults, not final flight values.
