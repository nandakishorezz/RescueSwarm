# Multi-ArduCopter SITL operations

The current installed ArduPilot source is inspected before a run. Stage 9S uses
official `sim_vehicle.py` multi-instance flags and derives endpoints from the
installed tool's instance rule. Every endpoint is numeric loopback and passes
`SITLSafetyGuard`; every heartbeat is checked for ArduPilot, ArduCopter, and its
expected unique SYSID.

The two-vehicle gold mission sequences launch, runs parallel AUTO missions,
feeds synthetic Stage 5 targets into a shared registry, allows one vehicle to
execute the existing Stage 7S delivery flow while the other remains in AUTO,
then verifies RTL, landing, and disarm for both. Separate campaigns cover
vehicle failure/reassigned coverage, one-action fleet recall, and a three-SITL
registration/planning smoke test.

No run touches physical hardware. Stop any stale local SITL processes before a
campaign. Each campaign writes launcher logs, identities, missions, telemetry,
search trails, union coverage, separation metrics, targets, assignments, and a
summary under `data/fleet_runs/<run_id>/`.
