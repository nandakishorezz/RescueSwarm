# Stage 9S — multi-drone fleet coordination

Stage 9S wraps the frozen single-vehicle rescue agent with one deterministic
`FleetMissionCoordinator`. It changes ownership of work, never the proven
single-drone method used to execute AUTO search, GUIDED target service,
simulated delivery, resume, RTL, and landing.

```text
one mission boundary
  -> Stage 3 global CoveragePlan
  -> contiguous minimax workload partition
  -> per-vehicle CoveragePlan and mission fingerprint
  -> one VehicleRescueAgent per guarded loopback ArduCopter SITL
  -> one FleetGateway / one GCS
```

Hardware Gate H1 remains **PENDING**. All command evidence is local ArduCopter
SITL evidence. Targets, reacquisition, and payloads remain synthetic. Stage 9S
adds no serial, USB, LAN, public network, servo, GPIO, or physical-flight path.

## Planning and ownership

The partitioner consumes Stage 3's ordered safe segments. Exact dynamic
programming splits that order into contiguous ranges while minimizing the
largest assigned cost. Cost includes path distance and turn cost, plus launch
transit in the reported metrics. Validation requires every global segment
exactly once, no duplicate ownership, valid vehicles, deterministic order, and
contiguous assignments. A partition fingerprint and per-vehicle plan
fingerprints make uploads auditable.

`SectorAssignmentManager` owns search work. `AssignmentLeaseManager` separately
owns target delivery. Search ownership never implies delivery ownership.
Versioned leases enforce at most one active vehicle per target, and a delivered
or ambiguous target is never automatically allocated another payload.

## Shared target registry

Confirmed Stage 5 estimates from all vehicles enter one registry. Association
uses ENU distance and uncertainty overlap. Each canonical survivor retains the
observing vehicle IDs, observation IDs, frame evidence, uncertainty, and time
range. Fusion remains uncertainty-dominated and preserves a common-bias floor;
two standard-GNSS observations are not represented as precision GNSS.

## Progress, safety, and recovery

Fleet coverage is the geometric union of buffered `SEARCHING` trails clipped to
the safe region. Launch, transit, GUIDED diversion, RTL, and landing do not count.
The separation monitor records pairwise horizontal and vertical distance.
Sequenced launch requires actual departure-clear altitude before the next launch;
landing uses one slot at a time. The live profile also uses explicitly labeled
SITL-only vertical layers. A normally completing vehicle holds at its last
mission item until the coordinated landing gate grants its RTL slot. Native
failsafe and emergency RTL are never delayed by this gate. None of these values
are approved physical separation settings while H1 is pending.

Every command needs an explicit application vehicle ID and nonzero MAVLink
target system. Broadcast is rejected except an explicitly authorized fleet-wide
`SAFETY_ABORT` or `EMERGENCY_RECALL`, currently mapped to RTL in SITL. Duplicate
SYSIDs fail startup. A stale, failed, returning, landing, or native-failsafe
vehicle receives no new work.

On vehicle failure, incomplete segments become `REASSIGN_REQUIRED`. A healthy
vehicle receives a newly generated and verified recovery mission. The failed
vehicle's native RTL/LAND is never overridden. Fleet recovery snapshots preserve
plans, vehicles, sectors, targets, leases, payload counts, and completed work.

## Running live evidence

Official ArduPilot must already be installed locally. The script uses the
installed `sim_vehicle.py --count --auto-sysid --auto-offset-line` support and
verifies each real heartbeat before commands.

```powershell
$env:ARDUPILOT_HOME='/home/nandakishore7/ardupilot'
python scripts/run_stage9s_fleet.py --scenario gold
python scripts/run_stage9s_fleet.py --scenario failure
python scripts/run_stage9s_fleet.py --scenario recall
python scripts/run_stage9s_fleet.py --scenario scalability --count 3
python tools/benchmark_fleet_planning.py --iterations 20 --fleet-size 2
```

Run artifacts are written under `data/fleet_runs/`. The primary live report is
copied to `STAGE_9S_REPORT.md`.
