# STAGE 12U REPORT

## Verdict

**STAGE 12U COMPLETE — GCS FINAL UI RELEASE CANDIDATE**

- Architecture changed: **NO**
- Hardware Gate H1: **PENDING**
- Physical hardware touched: **NO**
- Evidence class: local development-browser, automated frontend/backend tests, and offline production build

Stage 12U hardens the Stage 11S spatial digital twin. It adds no autonomy, flight mode, payload, fleet-planning, database, or geolocation capability.

## UI acceptance

| Area | Result | Evidence |
|---|---|---|
| Setup | PASS | Six numbered steps, actionable readiness blockers, software/physical readiness separation, protected Start action |
| Mission | PASS | Dominant state/coverage strip, map-first layout, compact fleet/target rails, protected abort/recall |
| Engineering | PASS | Ten organized diagnostic sections, geometry/layer controls, provenance, local render metrics |
| Review | PASS | Recorded-replay identity, seek/event stepping, filters, target shortcuts, print-safe summary |
| Loading/empty/error/stale states | PASS | Explicit component state and last-known-data handling; no traceback exposure |
| Competition lock | PASS | Engineering navigation absent and prohibited mission controls remain absent/rejected |

## Responsive results

The final production bundle was exercised in local headless Chrome. Every required surface was visible and every case reported **0 px horizontal overflow**.

| Case | Result | Required surfaces |
|---|---|---|
| 1366×768 Setup | PASS | Header, navigation, readiness/alert surface, Start |
| 1366×768 Mission | PASS | Mission state, map, fleet, alerts, emergency controls |
| 1536×864 Split | PASS | Synchronized map + 3D, mission state, fleet rail |
| 1920×1080 Mission | PASS | Wide map-first hierarchy and secondary panels |
| 1920×1080 Engineering 3D | PASS | Digital twin, layer controls, axes/provenance, metrics |
| 1366×768 Review | PASS | Replay controls, timeline, event list, no overflow |
| 125% zoom | PASS | Effective 1093×614 CSS px; critical Mission surfaces remain reachable |

The acceptance probe is reproducible with `python scripts/run_stage12u_browser_acceptance.py`. It rejects non-loopback origins.

## Visual and operator hardening

- Compact persistent status bar: mission identity, execution source, competition state, fleet state, elapsed time, local-offline status, critical count, simulation and H1 provenance.
- High-level mission state remains dominant; detailed telemetry stays in Engineering.
- Fleet, Targets, Timeline, and ancillary panels can collapse without shrinking the map unnecessarily. Split view supports 35/65, 50/50, and 65/35.
- Target and vehicle states use labels/shapes/icons in addition to colour. Stale telemetry shows age instead of remaining green.
- Long IDs use truncation, tooltip, and local copy controls.
- Confirmation is limited to Start, Safety Abort, and Emergency Recall.
- Empty, loading, reconnecting, stale, and component-specific error states are explicit.

## Accessibility

| Check | Result |
|---|---|
| Keyboard-reachable primary controls | PASS |
| Visible `:focus-visible` treatment | PASS |
| State not encoded by colour alone | PASS |
| Accessible labels/landmarks for primary controls | PASS |
| Reduced-motion preference | PASS |
| Desktop contrast/readability audit | PASS |

This is a source/automated desktop acceptance gate, not a formal assistive-technology certification.

## Bundle and loading performance

| Metric | Stage 11 baseline | Stage 12 final |
|---|---:|---:|
| Initial application JS | 1,819.66 kB | 211.56 kB |
| Initial application JS gzip | 493.35 kB | 67.22 kB |
| Initial JS reduction | — | 88.4% raw / 86.4% gzip |

Setup, Mission, Engineering, Review, spatial views, and the Three/MapLibre integrations are loaded on demand. Final notable lazy chunks:

- MapLibre view: 1,064.91 kB / 288.16 kB gzip
- Three.js: 481.99 kB / 121.11 kB gzip
- Digital twin view: 28.02 kB / 8.26 kB gzip
- Engineering page: 18.31 kB / 5.33 kB gzip
- Review page: 11.50 kB / 3.61 kB gzip

The Vite >500 kB warning remains for the isolated MapLibre chunk. It is documented rather than hidden by raising the warning limit. Normal initial UI no longer downloads that code until the spatial view needs it.

## Render and lifecycle evidence

The 1920×1080 Engineering scene measured **100 FPS, 7.0 ms P50, 14.0 ms P95, and 20 Three.js objects** in local headless Chrome with software rendering. These are development-machine diagnostics, not Pi 4 performance or a physical flight guarantee.

The accelerated lifecycle soak performed six measured in-app cycles through Engineering 3D → Review → Mission 2D after warm-up and forced garbage collection:

- used JS heap: 26.67 MB → 24.56 MB
- DOM node counter: 4,108 → 3,365
- event listeners: 1,366 → 938
- unbounded growth observed: **NO**

The code also explicitly cancels animation frames, disconnects resize observers, disposes controls/geometries/materials/render lists/renderers, releases WebGL context, removes canvases, and removes MapLibre instances/listeners. Render trails, recent events, observations, and timeline DOM are bounded/downsampled.

This accelerated lifecycle test is not represented as a 30-minute real-time soak. Pi 4 browser performance and a physical-duration soak remain unmeasured.

## Operator walkthrough

**PASS** for the local software workflow:

1. Open GCS and review persistent SITL/H1 provenance.
2. Load and validate the mission boundary.
3. Review fleet, plan, payload, and readiness blockers.
4. Start with one protected operator action.
5. Monitor mission state, fleet, targets, coverage, delivery, alerts, and camera geometry.
6. Observe terminal state and open Review.
7. Replay persisted telemetry/events and seek target/mission milestones.

Manual mission-control interventions after Start: **0** in the normal software workflow. Stage 12 did not rerun or claim a new live ArduCopter mission; it preserves the proven Stage 7S–10S SITL evidence and validates its operator presentation.

## Problems discovered and fixed

1. Review replay could expand to roughly 3,412 px at 1366×768 due to intrinsic flex/grid widths. Review containers now have bounded minimum/maximum widths; final overflow is 0 px.
2. MapLibre, Three.js, Engineering, and Replay code was largely eager. Route/view lazy loading reduced initial application JS by 88.4%.
3. A custom digital-twin layer could request repaint continuously after it no longer needed to. The unconditional repaint loop was removed and cleanup was strengthened.
4. Mission state hierarchy could show planning metadata over actual terminal/flight state, and stale `connected` flags could inflate the active-fleet count. Evidence-backed terminal/mode state and fresh link state now win.
5. Setup, mission, engineering, and replay controls lacked one consistent hierarchy at laptop resolution. They now use the shared release design system and collapsible layout.
6. The host already had unrelated services bound to port 8000. Acceptance ran on isolated loopback port 8120, and the probe now accepts an explicit loopback base URL.

## Verification

- Python regression: **296 passed, 9 skipped**
- Skips: three H1 physical tests, physical Pi camera test, and five opt-in live-SITL suites
- Stage 12U release gates: **7 passed** (included in the Python total)
- Frontend: **32 passed** across 7 files
- Production TypeScript/Vite build: **PASS**
- Local browser acceptance: **7/7 responsive cases PASS**
- Accelerated lifecycle soak: **PASS**
- Offline compliance: **PASS**, zero violations
- Combined executed passing tests: **328**

## Remaining limitations and physical gaps

1. Pi 4 browser FPS, memory, WebGL support, and long-duration UI performance are **NOT MEASURED**.
2. The MapLibre lazy chunk remains large, though it no longer blocks initial shell loading.
3. The offline basemap intentionally uses a local blank style until local map/terrain data is provisioned; operational geometry remains available.
4. No formal screen-reader or physical first-time-operator study was performed.
5. Vehicle dimensions, OV9281 intrinsics/FOV, camera extrinsics, motor telemetry, live real video, terrain, payload, and physical fleet evidence remain pending.

## Artifacts

- `data/stage12u_screenshots/browser_acceptance.json`
- `data/stage12u_screenshots/*.png`
- `data/stage12u_screenshots/pytest.xml`
- `docs/gcs_screenshot_index.md`
- `docs/gcs_user_guide.md`
- `SOFTWARE_UI_RELEASE_MANIFEST.json`
- `OFFLINE_COMPLIANCE_REPORT.md`

## Final status

```text
GCS SOFTWARE: FINAL UI RELEASE CANDIDATE
HARDWARE GATE H1: PENDING
PHYSICAL HARDWARE TOUCHED: NO
```

Next required work is Hardware Gate H1. No new software architecture stage is created.
