# Ground Control User Flow

## Setup Mode

1. Create a named mission record.
2. Select one GeoJSON or KML polygon file. Coordinates are interpreted as WGS84 longitude/latitude.
3. Import. The server enforces extension/size limits, coordinate ranges, unique vertices, nonzero geodesic area, and valid non-self-intersecting geometry.
4. Review area and any configurable NIDAR limit warning.
5. Validate. Camera and real GPS remain `NOT VERIFIED`; the Stage 2 vehicle is clearly `SIMULATED`.
6. Mission planning remains visibly unavailable until Stage 3.

## Engineering Mode

Development mode exposes GCS-event simulation, WebSocket diagnostics, the latest raw envelope, API/database/offline health, and coordinate inspection. Clicking the map only reads a coordinate; it cannot command a vehicle. Competition mode hides this page and rejects its APIs.

## Mission Mode

The map dominates the workspace. A compact right rail contains the local placeholder video, current vehicle state, and survivors. Search progress and recent important events run along the bottom. Every simulated field is labelled. Vehicle history is bounded to 500 map points and events to 100 records in browser memory.

Telemetry newer than two seconds is `CONNECTED`, two through five seconds is `DEGRADED`, and older telemetry is `STALE`. A vehicle explicitly reporting disconnected is `DISCONNECTED`. WebSocket failures are visible and reconnect with bounded exponential backoff.

## Review Mode

The review reads persisted mission state, area, duration, coverage, target/delivery counts, final vehicle battery, warnings, and the mission event timeline. Stage 2 does not yet provide time-scrubbable flight replay or full-rate flight log artifacts.

