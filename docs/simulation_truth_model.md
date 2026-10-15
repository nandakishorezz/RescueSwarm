# Simulation truth firewall

Perfect simulator state is represented by types in `rescue_core.simulation.truth`, including `WorldTruthVehicleState` and `WorldTruthSurvivor`. These are deliberately different from MAVLink estimated poses and canonical RescueSwarm targets.

Allowed consumers are world generation, the simulation observer, the evaluator, and simulation tests. Autonomy, vision, target management, mission orchestration, fleet allocation, planning, and autopilot adapters may not import the truth package. A static import-boundary test enforces this rule.

Truth is available only under the opt-in engineering namespace `/api/simulation/observer/*`. Competition Mode returns 403, and normal mission/status snapshots contain no survivor coordinates. Placing or spawning a survivor creates no Target, Observation, or DeliveryAssignment.

Artifacts prefixed `SIMULATION_ONLY_GROUND_TRUTH` are evaluator evidence and must never be routed into detector or autonomy inputs.

The conventional run filenames `survivor_ground_truth.json`, `wind_truth.jsonl`, and `vehicle_truth.jsonl` contain only redirect records with `contains_ground_truth: false` and `autonomy_ingestion_permitted: false`. Perfect state exists only in the conspicuously prefixed observer-only artifacts; it is not duplicated into a less explicit filename.
