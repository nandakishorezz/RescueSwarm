# RescueSwarm — Survivor Detection System

Autonomous multi-drone search-and-rescue platform with YOLO-based person detection, ArduPilot SITL simulation, and a full-stack GCS web application.

---

## Project Structure

```
SURVIVOR_DETECTION/
│
│   README.md                     ← You are here
│   pyproject.toml                ← Python project (pip install -e ".[dev]")
│   SETUP_AND_LAUNCH_GAZEBO.ps1   ← One-click Gazebo + ArduPilot launcher
│   launch.sh                     ← WSL launch helper
│   test_all.py                   ← Quick smoke test runner
│   test_env.py                   ← Environment check (Python, YOLO, MAVLink)
│
├── ground/                       GCS — Ground Control Station app
│   ├── backend/                  Python FastAPI server (port 8000)
│   │   └── app/
│   │       ├── api/              HTTP + WebSocket route handlers
│   │       ├── domain/           Core models (Mission, Target, Boundary)
│   │       ├── fleet/            Multi-drone coordination
│   │       ├── planning/         Coverage path planner
│   │       ├── repositories/     SQLite persistence
│   │       ├── resilience/       Failsafe / safety monitor
│   │       ├── services/         WebSocket manager, telemetry
│   │       ├── simulation/       Sim world generator, evaluator
│   │       └── sitl/             ArduPilot SITL launcher + surveillance
│   └── frontend/                 React + Vite GCS UI (port 5173)
│       └── src/
│           ├── app/              Root App + router
│           ├── components/       Map, panels, camera feed, trail
│           ├── hooks/            WebSocket hooks
│           ├── pages/            Mission, Setup, Review, Simulation pages
│           ├── services/         REST API client
│           ├── stores/           Zustand state (GCS, mission)
│           └── types/            TypeScript domain types
│
├── rescue_core/                  Core autonomy library (pip install -e .)
│   ├── adapters/                 MAVLink adapter
│   ├── autonomy/                 Mission state machines
│   ├── common/                   Shared geometry, time, logging utilities
│   ├── geolocation/              Pixel → GPS geolocation pipeline
│   ├── hardware/                 Pi camera and hardware drivers
│   ├── qualification/            Evidence and qualification logic
│   ├── resilience/               Fault injection and recovery
│   ├── simulation/               Gazebo interface and evaluators
│   ├── terrain/                  Elevation models
│   └── vision/                   YOLO inference pipeline
│
├── simulation/                   Gazebo simulation assets
│   ├── assets/                   3D models, survivor meshes, textures
│   ├── patches/                  Gazebo/ArduPilot plugin patches
│   ├── profiles/                 Camera and drone simulation profiles
│   └── worlds/                   Generated Gazebo SDF world files (auto-written)
│
├── data/                         All runtime and training data (gitignored)
│   ├── calibration/              Camera intrinsic/extrinsic calibration YAMLs
│   ├── datasets/                 ML training datasets
│   ├── training/                 YOLO training runs and weights
│   │   └── stage13sim_nadir_person_v3/training_runs/primary/weights/
│   │       └── best.pt           ← PRODUCTION person-detection model
│   ├── simulation_runs/          SITL run artifacts and telemetry
│   ├── rescue_runs/              Rescue mission records
│   ├── vision_runs/              Per-frame inference logs
│   ├── qualification_runs/       Formal qualification outputs
│   └── ground/                   GCS SQLite database
│
├── models/                       ML model weights
│   ├── yolo11n.pt                Pretrained YOLOv11-nano base
│   ├── manifest.yaml             Model registry
│   ├── artifacts/                Exported ONNX / TFLite formats
│   └── development/              Experimental checkpoints
│
├── config/                       YAML system configuration
│   ├── ground.yaml               GCS backend settings
│   ├── vision.yaml               YOLO model and threshold settings
│   ├── geolocation.yaml          Camera FOV and pipeline params
│   ├── rescue_orchestration.yaml Rescue mission parameters
│   ├── resilience_scenarios.yaml Fault injection scenarios
│   └── ...
│
├── scripts/                      Automation, qualification, training scripts
│   ├── run_stage13sim_phase4.py  Full Stage 13 SITL qualification
│   ├── build_stage13sim_*.py     Dataset builders
│   ├── run_stage*.py             Per-stage qualification runners
│   └── ...
│
├── tools/                        Developer utility scripts
│   ├── camera_probe.py           Detect cameras
│   ├── mavlink_probe.py          Scan MAVLink devices
│   ├── evaluate_vision.py        Benchmark YOLO on dataset
│   └── ...
│
├── tests/                        Automated unit and integration tests (pytest)
│   ├── test_geolocation_*.py
│   ├── test_vision_*.py
│   ├── test_stage*.py
│   └── ...
│
├── deployment/                   Raspberry Pi deployment configs
│   ├── pi4/
│   └── pi5/
│
├── docs/                         Documentation and evidence
│   ├── architecture.md
│   ├── gcs_user_guide.md
│   ├── simulation_*.md
│   ├── evidence/                 Qualification evidence (JSON/JSONL/MD)
│   └── release/                  Release packages
│
└── archive/                      Old dev scripts (not maintained)
    ├── analysis_scripts/
    ├── browser_tests/
    └── patch_scripts/
```

---

## Quick Start

### 1. Install Python dependencies
```bash
pip install -e ".[dev]"
```

### 2. Start Gazebo + ArduPilot SITL
```powershell
# Windows (PowerShell)
.\SETUP_AND_LAUNCH_GAZEBO.ps1
```

### 3. Start the GCS backend
```bash
cd ground/backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 4. Start the GCS frontend
```bash
cd ground/frontend
npm install
npm run dev
# Open http://127.0.0.1:5173
```

### 5. Run tests
```bash
pytest                  # full suite
python test_all.py      # quick smoke test
python test_env.py      # environment check
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Browser (Chrome)  http://127.0.0.1:5173                │
│  React GCS UI — Map, Camera Feed, Mission Controls      │
└──────────────────────┬──────────────────────────────────┘
                       │ WebSocket + REST
┌──────────────────────▼──────────────────────────────────┐
│  FastAPI Backend    http://127.0.0.1:8000               │
│  • Mission planner  • SITL manager  • Surveillance svc  │
└──────┬──────────────────────────┬───────────────────────┘
       │ MAVLink (TCP 5761)        │ TCP socket (port 45678)
┌──────▼──────────┐    ┌──────────▼──────────────────────┐
│  ArduPilot SITL │    │  WSL: camera_stream_helper.py    │
│  (WSL / Linux)  │    │  Gazebo camera → TCP frames      │
└──────┬──────────┘    └─────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│  Gazebo Simulator  (WSL)                                │
│  World SDF + Drone model + OV9281 camera sensor         │
└─────────────────────────────────────────────────────────┘
```
