# simulation/ — Gazebo Simulation Assets

All files needed to generate and run the Gazebo simulation environment for RescueSwarm.

## Sub-directories

```
simulation/
├── assets/     3D models, textures, survivor models used in Gazebo worlds
├── patches/    Patches applied to Gazebo/ArduPilot plugin source
├── profiles/   Camera and drone simulation profiles (JSON/YAML)
└── worlds/     Generated Gazebo world SDF files
```

Worlds are generated at runtime by the backend (`ground/backend/app/simulation/`) based on the mission boundary and chosen profile. Do not edit world files manually — they are overwritten on each `PREPARE` step.
