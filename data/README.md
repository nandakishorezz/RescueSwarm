# data/ — All Runtime and Training Data

All data produced or consumed at runtime. **Not committed to git** (see `.gitignore`).

## Sub-directories

```
data/
├── calibration/          Camera intrinsic/extrinsic calibration files
│   └── cameras/          Per-camera calibration YAML files
├── datasets/             ML training datasets
│   └── aerial_human_baseline/
├── training/             YOLO training runs and weights
│   └── stage13sim_nadir_person_v3/
│       └── training_runs/primary/weights/
│           ├── best.pt        ← YOLO model used in production
│           └── last.pt
├── simulation_runs/      Raw telemetry + artifacts from SITL runs
├── rescue_runs/          Rescue mission execution records
├── vision_runs/          Per-frame inference logs
├── qualification_runs/   Formal qualification run outputs
├── ground/               GCS SQLite database and state
│   ├── ground.db
│   └── ground_v2.sqlite
└── uploads/              User-uploaded boundary GeoJSON files
```
