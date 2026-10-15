# Simulation Lab operator guide

1. Open **SIMULATION** (unavailable in Competition Mode).
2. Review the reference Drone and Camera profiles; all unmeasured values show provenance.
3. Load a validated mission in Setup, then review Mission World.
4. Configure wind and its deterministic seed.
5. Place standing/lying survivor world objects on the preview ground. This does not create autonomy targets.
6. Select **VALIDATE + GENERATE**. Resolve all readiness errors.
7. Select **START SIMULATION**. Physics-critical configuration locks until stop.
8. Observe MAVLink-estimated vehicle state. Enable **OBSERVER GROUND TRUTH** only for laboratory comparison.
9. Select **STOP SIMULATION** to terminate owned Gazebo and ArduPilot processes.

The page always identifies `SITL_GAZEBO`, `NO PHYSICAL AIRCRAFT`, and H1 PENDING. Phase 1 does not detect survivors; hidden models should not appear as mission targets.
