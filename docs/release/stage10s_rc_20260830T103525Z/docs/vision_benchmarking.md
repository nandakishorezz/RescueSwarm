# Vision benchmarking

Use the same recorded frames when comparing models. Do not compare candidates using different scenes and call the difference a model result.

## Three benchmark modes

Capture-only isolates acquisition:

```bash
python tools/benchmark_vision.py --mode capture_only --source synthetic --duration 60
```

Inference-only uses deterministic local images:

```bash
python tools/benchmark_vision.py --mode inference_only --source image_directory \
  --input datasets/aerial_human_baseline/images --loop \
  --backend ncnn --model yolo11n_person_ncnn --duration 60
```

End-to-end uses acquisition, latest-frame queuing, preprocessing and detection:

```bash
python tools/benchmark_vision.py --mode end_to_end --source picamera2 \
  --profile detection --backend ncnn --model yolo11n_person_ncnn \
  --duration 60 --save-samples
```

For mission-length thermal/stability evidence use `--duration 1800`. Ordinary tests never run an endurance benchmark.

Artifacts are stored under `data/vision_runs/<timestamp>_<id>/` with system, camera, model and run manifests; frame/detection NDJSON; summary/report; and bounded samples. The summary separates capture rate, frames presented to inference, detector output rate, drops, preprocessing, inference, postprocessing, capture-to-result latency, frame age, CPU, RSS, temperature, throttling, errors and memory growth.

Compare runs:

```bash
python tools/compare_vision_runs.py data/vision_runs/RUN_A data/vision_runs/RUN_B --output VISION_COMPARISON.md
```

Evaluate only when real labels exist:

```bash
python tools/evaluate_vision.py data/vision_runs/RUN datasets/aerial_human_baseline
```

With no label files, evaluation returns `ACCURACY NOT EVALUATED — LABELLED DATASET REQUIRED`. Synthetic evaluation tests validate the mathematics only; they are not model-accuracy evidence.

The initial engineering targets are P95 end-to-end latency below 200 ms, at least 5 useful detector outputs per second, no thermal throttling, no memory growth, and no unbounded queue. Report the measured result even when it misses a target.
