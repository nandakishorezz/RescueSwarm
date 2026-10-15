# MAVLink mission protocol

Stage 3 uses MAVLink 2 and `MISSION_ITEM_INT`. The command-capable adapter is SITL-only; the Stage 1 read-only hardware adapter is unchanged.

```text
ArduPilot-managed home slot (sequence 0)
NAV_TAKEOFF
DO_CHANGE_SPEED
NAV_WAYPOINT ...
NAV_RETURN_TO_LAUNCH
```

ArduPilot reserves sequence 0 as its dynamic home record, so `NAV_TAKEOFF` is the first executable command at sequence 1. Verification canonicalizes only this controller-managed home data and ArduPilot's equivalent integer/global-relative frame representation; all executable commands, coordinates, altitudes, and critical parameters remain strict.

Search coordinates use `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT` and degrees multiplied by 10,000,000. RTL is explicit. Every item uses `MAV_MISSION_TYPE_MISSION`; fences and rally points are never uploaded.

Upload is receiver-driven: the GCS sends `MISSION_COUNT`, answers each `MISSION_REQUEST_INT`, accepts duplicate or out-of-order valid requests, rejects invalid sequences and negative acknowledgements, and uses bounded retries. After acknowledgement, it downloads every integer item and compares count, sequence, command, frame, coordinates, altitude, critical parameters, and a canonical SHA-256 fingerprint. A mismatch prevents arming.

One `MAVLinkMessageRouter` thread owns `recv_match()`. Typed queues deliver protocol responses, command acknowledgements, and telemetry without consumers stealing messages. `COMMAND_ACK` is required for arm and mission-start commands; mode changes are confirmed by heartbeat.
