# Drone profiles

`DroneProfile` is a versioned, unit-bearing schema for frame layout, mass, dimensions, inertia, rotors, thrust axes, motor constants, drag, payload/battery mass, and motion limits. QUAD_X, QUAD_PLUS, HEXA_X, OCTA_X, and CUSTOM layouts are validated; live Phase 1 qualification covers QUAD_X only.

Every scalar/vector declares provenance: `PHYSICAL_MEASURED`, `PHYSICAL_UNVERIFIED`, `REFERENCE_PRESET`, `ENGINEERING_ESTIMATE`, or `SIMULATION_ONLY`. The bundled `rescueswarm_reference_quad.yaml` follows official Iris reference dynamics and is not a measurement of a RescueSwarm aircraft.

The builder generates collision, visual, inertial, rotor/joint, LiftDrag, ApplyJointForce, IMU, camera, and ArduPilot plugin SDF. Motor outputs create rotor forces; Gazebo physics produces pose; attitude is never derived from PWM in RescueSwarm.

Invalid mass, inertia, dimensions, layouts, rotor duplication, thrust axes, and speed values fail before launch.
