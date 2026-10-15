# tools/ — Developer Utilities

Standalone utility scripts for hardware probing, calibration, vision benchmarking, and offline compliance checks. These run on the development machine (Windows or Pi).

## Key Tools

| Script | What it does |
|--------|-------------|
| `camera_probe.py` | Detect and inspect connected cameras |
| `mavlink_probe.py` | Scan serial ports, verify MAVLink heartbeat |
| `detect_serial_ports.py` | List all serial devices |
| `calibrate_camera.py` | Run OpenCV camera calibration |
| `evaluate_vision.py` | Benchmark YOLO inference on a dataset |
| `evaluate_geolocation.py` | Measure geolocation accuracy from logs |
| `check_offline_compliance.py` | Verify system meets offline operation requirements |
| `system_info.py` | Print system specs (CPU, RAM, GPU) |
| `export_model.py` | Convert `.pt` to ONNX/TFLite |
| `benchmark_vision.py` | FPS benchmark for YOLO on target hardware |

## Usage

```bash
python tools/camera_probe.py
python tools/mavlink_probe.py --port /dev/ttyUSB0
python tools/evaluate_vision.py --model data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt
```
