# rescue_core/ — Core Drone Autonomy Library

The shared Python library that implements all drone autonomy, vision, geolocation, and resilience logic. Used by both the GCS backend and standalone scripts.

## Sub-directories

```
rescue_core/
├── adapters/       MAVLink / hardware adapter interfaces
├── autonomy/       Mission execution state machines
├── common/         Shared utilities (geometry, time, logging)
├── geolocation/    Pixel-to-GPS geolocation pipeline
├── hardware/       Physical hardware drivers (Pi, camera)
├── qualification/  Evidence collection and qualification logic
├── resilience/     Fault injection and recovery policies
├── simulation/     Gazebo simulation interfaces and evaluators
├── terrain/        Terrain elevation models
└── vision/         YOLO inference pipeline, model management
```

## Install

```bash
pip install -e ".[dev]"
```
