# Fleet architecture and invariants

The fleet layer answers only three questions: who searches where, who services
which canonical target, and what work remains. Every vehicle retains its own
Stage 7S/8S rescue and safety agent and one authoritative MAVLink receive loop.

Core components are:

- `FleetCoveragePartitioner`: deterministic connected workload partitions.
- `VehicleRegistry`: application IDs, unique SYSIDs, independent freshness and health.
- `SharedTargetRegistry`: mission-wide evidence and duplicate suppression.
- `DeliveryAllocator` and `AssignmentLeaseManager`: safe, atomic ownership.
- `FleetProgressTracker`: geometric-union search-only coverage.
- `LaunchLandingScheduler`, `CoordinatedLandingGate`, and
  `FleetSeparationMonitor`: ordered shared-zone use and time-aligned separation evidence.
- `FleetMissionCoordinator`: fleet state, reallocation, recall, and snapshots.
- `FleetCommandRouter`: explicit target routing and authorized safety broadcast.

Hard runtime/test invariants include unique vehicle IDs and SYSIDs; one active
owner per segment, target, and payload slot; no new work for stale/failsafe
vehicles; no unspecified command target; no broadcast except fleet safety; no
repeat assignment after verified delivery; no negative inventory; and no fleet
completion until every deployed vehicle is safe and terminal.

The local TCP transport used by multi-SITL is an adapter detail. Fleet domain
models do not depend on it, leaving future local Pi/radio transport replaceable.
