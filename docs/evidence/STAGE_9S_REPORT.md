# Stage 9S Fleet Mission Report

## Hardware Gate

- H1: **PENDING**
- Physical hardware touched: **NO**
- Evidence class: **ARDUCOPTER SITL / SYNTHETIC TARGET AND PAYLOAD**

## Fleet

- Configured vehicles: 2
- Registered vehicles: 2
- SYSIDs: [1, 2]
- ArduPilot version(s): ['4.8.0']
- ArduPilot commit: af2a1bafc8ab993418e6714cbc2674dcc178d11a
- Connections: {'drone_02': 'tcp:127.0.0.1:5770', 'drone_01': 'tcp:127.0.0.1:5760'}

## Partition

- Boundary area: 2560.00 m²
- Global path cost: 340.00 m
- Per-vehicle assigned cost: {'drone_01': 148.32455532103373, 'drone_02': 220.09072203455355}
- Imbalance: 38.96%
- Estimated makespan: 32.00 s

## Parallel search

- Per-vehicle coverage: {'drone_01': 45.63251197801199, 'drone_02': 69.37738905825161}
- Combined geometric-union coverage: 99.57%
- Minimum separation: 8.24563203990159 m
- Separation warnings / critical events: 0 / 0
- Launch order: ['drone_01', 'drone_02']
- Landing order: ['drone_02', 'drone_01']

## Targets and delivery

- Canonical targets: 1
- Cross-vehicle duplicate merges: 1
- Verified simulated deliveries: 1
- Duplicate delivery prevented: True
- Assignment leases: [{'assignment_id': '694c890b-fb9b-404e-bad2-00902d983af4', 'target_id': '8c865e7e-498e-5d2e-b8ec-1985c17ca718', 'vehicle_id': 'drone_01', 'payload_slot': None, 'created_at': '2026-08-30T08:13:20.726354Z', 'expires_at': '2026-08-30T08:14:20.726314Z', 'assignment_version': 1, 'state': 'COMPLETED', 'verification': True}]

## Failure recovery

- Failed vehicle: NOT_APPLICABLE
- Reassigned to: NOT_APPLICABLE
- Recovery mission completed: NOT_APPLICABLE

## Competition readiness

- Multi-drone coordination: **PASS — SITL**
- Area division / parallel search: **PASS — SITL**
- Shared target / delivery allocation: **PASS — SITL/SYNTHETIC**
- Physical fleet, payload, video, failsafes, mass: **PENDING**
- Overall: **NOT COMPETITION READY**
