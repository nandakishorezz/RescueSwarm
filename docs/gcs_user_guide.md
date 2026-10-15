# RescueSwarm offline GCS user guide

The GCS is one local React/FastAPI application for one operator. Start the backend with `python scripts/run_ground_backend.py`, then open `http://127.0.0.1:8000`. A persistent `SIMULATION` badge identifies SITL evidence and the header keeps `PHYSICAL VALIDATION H1: PENDING` visible. The desktop interface is qualified for 1366×768, 1536×864, and 1920×1080; Raspberry Pi 4 browser performance remains unmeasured.

## SETUP

Follow the six numbered steps: **Mission**, **Fleet**, **Plan**, **Payload**, **Readiness**, and **Start**. Import the official GeoJSON or KML mission file, inspect area/perimeter/vertices and validation messages, then generate and review the global sweep, per-vehicle sectors, payload inventory, fleet identity, and readiness categories. Each blocked readiness item includes its reason. `SITL READY` and `PHYSICAL VALIDATION PENDING` are intentionally separate states.

Competition mode does not permit boundary or waypoint editing after import. One **Start Mission** action starts the coordinated mission; there are no per-drone start buttons. Start remains disabled until software/SITL readiness passes and asks for confirmation before the consequential action.

## MISSION

The spatial view is dominant. Switch between **2D Map**, **3D Digital Twin**, and **Split** without changing vehicle state. Split view supports 35/65, 50/50, and 65/35 layouts. Select a vehicle or target to synchronize the map, digital twin, telemetry cards, camera geometry, and detail drawer. Collapse the Fleet, Targets, or Timeline panels when more spatial room is needed; the local browser remembers the layout. These are display preferences only.

The command strip makes the mission state readable at a distance. Competition controls remain limited to **Start Mission**, **Safety Abort**, and **Emergency Recall**. Abort and recall require confirmation and always remain fleet-wide; there are no manual flight, waypoint, target-assignment, or payload controls.

The fleet panel answers where each aircraft is, its actual mode/task, telemetry freshness, sector, target assignment, and payload count. Stale telemetry remains visible as last-known data but is explicitly labelled with its age. The target panel separates detector confidence from geographic uncertainty and labels candidate, confirmed, assigned, approaching, delivering, delivered, unserviceable, and ambiguous states with text and shape as well as color. IDs can be copied without exposing internal paths.

## ENGINEERING

Engineering mode is organized into **System**, **Vehicles**, **Vision**, **Spatial**, **Camera**, **Targets**, **Failures**, **Network**, **Storage**, and **SITL**. It exposes camera/body/world axes, frustum, footprint, observation ray, copyable matrices, health freshness, local rendering metrics, and simulation-only geometry inputs. The layer menu controls aircraft, paths, sectors, frustum, footprint, rays, targets, uncertainty, and axes. **Reset View**, follow, top-down, chase, orbit, and side presets move only the display camera.

The geometry simulator always distinguishes `SIMULATED`, `CONFIGURED`, and `PHYSICAL UNVERIFIED`. These are visualization controls, not flight controls, and they do not update physical calibration records. Engineering routes and controls are absent in Competition mode.

## REVIEW

Review loads the persisted telemetry and event bundle. Playback is timestamp-driven and offers **Play/Pause**, seek, previous/next event, and 0.25×–4× rates. `Space` toggles playback while `Esc` closes supported drawers/dialogs. Vehicle, target, severity, and event-family filters keep large timelines usable. A selected target can jump to first detection, confirmation, or delivery. **Recorded Replay** is always distinct from live state. Recorded state remains authoritative; display interpolation is never persisted as telemetry.

The Review summary can be printed locally. Mission/fleet/evidence reports are exposed only through safe backend artifact routes; operator pages do not reveal host filesystem paths.

## Connection, loading, and error states

- `CONNECTED`, `RECONNECTING`, `STALE`, and `DISCONNECTED` distinguish ground-link conditions.
- During a brief reconnect, last-known mission state remains visible but is marked stale.
- Loading panels explain what is pending, such as `Loading mission…` or `Waiting for fleet telemetry…`.
- Empty panels explain the next action instead of showing broken charts.
- API errors name the affected component, retain the last successful update when available, and offer retry without showing a Python traceback.

## Accessibility and display controls

Primary controls are keyboard reachable and use visible focus outlines. Operational state uses text/icons/shapes as well as colour. Non-essential transitions honor `prefers-reduced-motion`. Long mission, target, calibration, and run identifiers use truncation plus title/copy affordances. Units are standardized to m, m/s, °, %, s, and min.

## Evidence labels

- `SITL`: produced by ArduCopter SITL.
- `SYNTHETIC`: simulated survivor, detection, or payload evidence.
- `CONFIGURED_ASSUMPTION`: digital-twin demonstration input.
- `PHYSICAL_UNVERIFIED`: requires Hardware Gate H1.
- `PHYSICAL_MEASURED`: allowed only after physical evidence exists.
- `UNKNOWN`: unavailable; displayed as an em dash where appropriate.

The current camera/video state is digital-twin simulation, not a physical live stream.

## Local acceptance check

With the local backend running, execute:

```powershell
python scripts/run_stage12u_browser_acceptance.py
```

The check drives a locally installed headless Chrome only against the configured loopback origin and writes screenshots plus `browser_acceptance.json` under `data/stage12u_screenshots/`. It does not test a physical Pi 4 browser or Hardware Gate H1.
