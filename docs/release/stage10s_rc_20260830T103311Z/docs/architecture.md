# Stage 1 Architecture

## Frozen decisions

- Autopilot firmware family: ArduCopter (no PX4 migration).
- Prototype controller: Pixhawk 2.4.8.
- Companion API: pymavlink/MAVLink behind `AutopilotAdapter`.
- Edge: Raspberry Pi 5, 8 GB; ground node: Raspberry Pi 4, 4 GB.
- GPS: u-blox M8N; camera: OV9281 global shutter.
- Offline operation is mandatory. There are no cloud, cellular, remote API, CDN, or online-map dependencies.
- ROS 2 and MAVSDK are not core dependencies.
- Stage 1 supports one aircraft; later capability models can represent a fleet.

## Package boundary

```text
config/prototype.yaml
        |
        v
VehicleCapabilities       DiagnosticResult
        |                        |
        +-------- rescue_core ---+
                    |
             AutopilotAdapter
                    |
        ArduPilotMavlinkAdapter
                    |
          pymavlink / MAVLink
                    |
              ArduCopter
```

`VehicleCapabilities` describes what is configured and separately records what was detected or verified. It prevents a YAML declaration from being mistaken for hardware evidence.

`AutopilotAdapter` exposes Stage 1 reads for heartbeat, identity, firmware, GPS, global position, attitude, heading, battery, health, capabilities, and a parameter snapshot. Future flight operations are named at this boundary but are deliberately implemented as safety exceptions. A later stage must perform a separate safety review before any write operation is introduced.

## Diagnostics data flow

Each tool accepts an output directory. A complete collection uses one timestamp directory:

```text
data/diagnostics/YYYY-MM-DD_HH-MM-SS/
├── system.json
├── serial.json
├── mavlink.json
├── mavlink_raw.jsonl
├── parameters.param
├── camera.json
├── camera_dmesg.txt
└── summary.md              # optional report destination
```

The default report destination is repository-root `STAGE_1_REPORT.md`. Missing files become `NOT_TESTED`; timeouts and observed negative evidence become `FAILED`; neither is rewritten as success.

## Stage boundary

This stage contains no detector, tracker, target geolocation, mission planner, autonomous approach, payload controller, swarm transport, GCS website, ROS 2 graph, database, or cloud service. Those are intentionally outside this foundation.

