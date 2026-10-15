# Stage 7S failure policies

- Target outside the operational region, excessive uncertainty, no payload, low battery, or insufficient time: record the confirmed target and do not divert.
- Target not reacquired: do not drop; retain/defer the target and resume search.
- Payload release failure or verification failure: never mark delivery complete; resume search when vehicle state remains healthy.
- GUIDED rejection, setpoint watchdog expiry, boundary violation, MAVLink staleness, or inconsistent control state: mission-threatening failure; request RTL in guarded SITL.
- Resume sequence or AUTO confirmation failure: request RTL in guarded SITL.
- Browser/WebSocket loss: UI becomes disconnected, but it is not treated as MAVLink loss.

No policy can send a command to the Stage 1/6 physical read-only adapter.
