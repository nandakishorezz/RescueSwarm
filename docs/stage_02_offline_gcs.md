# Stage 2 — Offline Ground Control System

Stage 2 adds a single local Ground Control System without connecting any real flight-control operation. Stage 1 diagnostics remain available and unchanged.

## Delivered behavior

- FastAPI REST API for health, readiness, offline status, mission creation, boundary import/validation, vehicles, targets, events, and review.
- One mission WebSocket at `/ws/mission/{mission_id}` using timestamped, sequenced envelopes.
- React/TypeScript/Vite application with Engineering, Setup, Mission, and Review modes.
- Local MapLibre blank style with no tile source. It renders WGS84 mission boundaries, vehicle positions, bounded trails, simulated coverage lines, and target states.
- SQLite repository mode for development and a native PostgreSQL/PostGIS schema for Pi 4 deployment.
- Deterministic `SimulatedVehicleSource`; it does not import or call the Stage 1 autopilot adapter.
- GeoJSON and KML parsing, safe upload limits, Shapely validity checks, ellipsoidal area, and PROJ topocentric ENU conversions.
- Runtime `OfflineGuard` plus a source/build compliance scanner.

## Start on Windows

From repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cd ground\frontend
npm install
npm run build
cd ..\..
python scripts\run_ground_backend.py
```

Open `http://127.0.0.1:8000`. The production frontend is served by FastAPI from local `dist/` assets.

For frontend hot reload, run the backend in one terminal and this in a second:

```powershell
cd ground\frontend
$env:VITE_API_ORIGIN="http://127.0.0.1:8000"
npm run dev
```

Open `http://127.0.0.1:5173`. These loopback addresses are development endpoints, not external runtime dependencies.

## Start on Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cd ground/frontend
npm install
npm run build
cd ../..
python scripts/run_ground_backend.py
```

For hot reload:

```bash
cd ground/frontend
VITE_API_ORIGIN="http://127.0.0.1:8000" npm run dev
```

## Demonstration

Import `data/examples/sample_boundary.geojson` in Setup Mode, validate it, enter Engineering Mode, then start the simulator. Or run the deterministic service-level demonstration:

```bash
python scripts/run_stage2_demo.py
```

The simulation creates `drone_01`, increases synthetic coverage, creates and confirms a simulated target, records a simulated delivery, returns, completes, and populates Review Mode. It is not ArduPilot SITL and makes no real vehicle connection.

## Verification

```bash
python -m pytest
cd ground/frontend
npm test
npm run build
cd ../..
python tools/check_offline_compliance.py
```

The application upload endpoint accepts only configured `.geojson`, `.json`, and `.kml` extensions, reads at most 1 MiB, never executes content, uses only the basename for parser selection, and stores structured geometry under generated mission identifiers rather than trusting an uploaded path.

## Deliberate Stage 2 limits

No real aircraft command API exists. There is no path planner, mission upload, camera stream, detector, geolocator, payload actuation, swarm coordinator, Redis, ROS, cloud, or external authentication. The video panel is a local generated test pattern labelled `SIMULATED`.

