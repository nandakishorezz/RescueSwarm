# Stage 10S software qualification

Stage 10S exercises the frozen RescueSwarm architecture rather than adding a mission-control layer.

## Qualification commands

```powershell
python scripts/run_stage10s_qualification.py
python scripts/benchmark_stage10s_setup.py --iterations 20 --sitl-iterations 2
python scripts/run_stage9s_fleet.py --scenario competition
python scripts/build_competition_release.py
```

The qualification runner uses the near-10-hectare fixture, repeats planning to verify deterministic fingerprints, measures memory and timing, tests a ten-target registry, validates payload- and deadline-limited outcomes, performs competition preflight checks, and records a runtime socket audit. Its mission outcome scenarios are explicitly deterministic software models, not live flight evidence.

The setup profiler reports three independent paths: import-to-plan-ready, import-to-fleet-missions-ready, and import-to-two-SITL-ready with actual heartbeat plus mission upload/download verification. These values are development-host software/SITL measurements. They are not the physical five-minute setup result.

The live `competition` fleet rehearsal runs two real ArduCopter SITL processes through guarded loopback endpoints. It uses synthetic Stage 5 perception/reacquisition and simulated payloads, while mode changes, mission progress, positions, search interruption/resume, RTL, landing, and disarm come from real MAVLink/SITL evidence.

## Evidence classes

- `DETERMINISTIC_SOFTWARE_MODEL`: pure deterministic policy/data-scale qualification.
- `SOFTWARE_SITL_SETUP_ONLY`: actual SITL readiness setup on the development host.
- `SITL_SYNTHETIC`: real ArduCopter SITL movement with synthetic targets and simulated payloads.
- `PHYSICAL`: absent in Stage 10S. Hardware Gate H1 remains pending.

## Release builder

`scripts/build_competition_release.py` builds the local frontend, runs the offline and redacted secret audits, validates available model hashes, hashes critical sources/configuration, and copies deployment inputs and reports into `release/stage10s_rc_<timestamp>/`. It performs no download and no package installation.

## Test classes

- `UNIT` and `INTEGRATION`: standard `python -m pytest` run.
- `SITL_SINGLE`: opt-in Stage 7S/8S live tests.
- `SITL_FLEET`: Stage 9S/10S scenario commands and opt-in fleet marker.
- `STRESS`: Stage 10S qualification/profile tests.
- `PHYSICAL`: opt-in and expected to skip until H1 hardware is available.

## Truthfulness

Passing Stage 10S means the software release-candidate gate passed. It does not verify the Raspberry Pi, OV9281, Pixhawk/M8N accuracy, flight endurance, RF link, physical failsafes, real video, payload mechanism, mass, launch-zone fit, aerial recall, or collision safety.
