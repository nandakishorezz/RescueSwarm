# Physical wind model

Phase 1 uses Gazebo Harmonic `WindEffects`. The world `<wind>` vector and deterministic sine/noise settings are generated from `WindProfile`; physical links opt into wind. The wind value is not sent to RescueSwarm autonomy.

Modes are CALM, CONSTANT, TURBULENT, and GUSTING. Every stochastic run has an explicit seed, and the planned truth schedule is persisted. Repeating a profile and seed produces an identical schedule.

The acceptance chain is physical: wind force changes rigid-body velocity/attitude, ArduPilot compensates with motor outputs, and the changed vehicle quaternion moves the attached camera/frustum/footprint. There is no display shake, pose teleport, fake quaternion, or `wind_offset` path.

These coefficients are simulation-only and are not physical certification levels.
