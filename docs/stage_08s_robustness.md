# Stage 8S — single-drone autonomy robustness

Stage 8S is a destructive software/SITL validation stage, not a new flight capability. It adds a central `SafetySupervisor`, conservative `RecoveryManager`, deterministic `FailureInjector`, mission invariants, durable recovery snapshots/outbox, and competition-mode command policy. Hardware Gate H1 remains **PENDING**.

Safety ownership is explicit: ArduCopter owns stabilization and native battery/EKF/geofence/link failsafes; RescueSwarm owns target-service timeouts, vision freshness, GUIDED watchdogs, payload state, and mission deadlines; the operator owns mission start, safety abort, and emergency recall. If actual ArduCopter mode is `RTL`, `LAND`, or `SMART_RTL`, recovery observes it and never forces AUTO/GUIDED.

Run the deterministic campaign:

```bash
python scripts/run_stage8s_fault_campaign.py
```

Artifacts are written to `data/resilience_runs/<run>/`. This normalized campaign tests safety policy and recovery deterministically; it does not claim native sensor-failsafe evidence. Run the live recall test separately against disposable official SITL:

```powershell
$env:ARDUPILOT_HOME='/home/nandakishore7/ardupilot'
$env:RESCUESWARM_RUN_SITL_RESILIENCE='1'
python -m pytest ground/backend/tests/test_sitl_stage8s_live.py -q -s
```

That test waits for actual AUTO search and actual GUIDED mode, triggers recall, proves no simulated payload release occurred, observes actual RTL, then waits for landing and disarm.

No Stage 8S path accepts serial, COM, LAN, hostname, or public command endpoints. Physical failure behavior remains unverified.
