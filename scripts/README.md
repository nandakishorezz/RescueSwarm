# scripts/ — Automation and Qualification Scripts

Long-running scripts for training, qualification runs, stage validation, and dataset building. These are meant to be run by a developer, not imported as a library.

## Categories

```
scripts/
├── build_*             Dataset and release builders
├── run_stage*          Per-stage qualification runners
├── run_sitl_*          SITL smoke tests
├── evaluate_*          Model/geolocation evaluators
├── analyze_*           Post-run analysis
├── benchmark_*         Performance benchmarks
├── capture_*           Hardware capture utilities
├── finalize_*          Post-qualification finalization
├── find_*              Hardware detection (serial ports, cameras)
├── generate_*          Report generators
├── install_*           WSL/environment setup scripts
├── inspect_*           Environment inspectors
├── probe_*             Asset probes
├── qualify_*           Causality qualification
├── select_*            Model checkpoint selection
├── summarize_*         Run summarizers
└── write_*             Evidence writers
```

## Example

```bash
# Run the full Stage 13 SITL qualification
python scripts/run_stage13sim_phase4.py

# Build the YOLO training dataset
python scripts/build_stage13sim_nadir_yolo_v3_dataset.py
```
