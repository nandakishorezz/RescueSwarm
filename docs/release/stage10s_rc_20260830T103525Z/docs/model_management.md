# Local model management

Mission runtime never downloads or exports a model. `models/manifest.yaml` is the traceable inventory. A deployable entry identifies model ID, local path, architecture, input size, classes, backend, SHA-256, source, license metadata, commercial notes, export time and role.

Missing artifacts, missing hashes, hash mismatches and unavailable runtime packages fail clearly. A development-time YOLO11n ONNX export is provisioned locally at `models/artifacts/yolo11n.onnx`, pinned by SHA-256 in the manifest, and verified with the Windows ONNX backend. The artifact directory is ignored by Git, so it must be copied explicitly with the deployment bundle. YOLO11n NCNN remains an unavailable provisioning record pending Pi 5 export/runtime verification. The mock entry is a deterministic software test double, not a detector.

Training/export belongs in a separate development environment. Given a model file already present locally:

```bash
python tools/export_model.py /local/path/model.pt --format ncnn --imgsz 640
python tools/export_model.py /local/path/model.pt --format onnx --imgsz 640
```

The exporter prints an artifact hash. Copy the artifact under `models/artifacts/`, update the manifest explicitly, review upstream model/dataset/exporter licensing, and copy the already-provisioned repository to the Pi. Never add `download-if-missing` behavior.

`NCNNDetectorBackend` is the preferred Pi CPU candidate and `ONNXDetectorBackend` is the local reference candidate. Replaceable YOLO11n and YOLO26n provisioning records are present so neither architecture is silently selected without measurements. No model has been benchmarked on the physical Pi 5 in the current Windows session. A generic COCO person model is always labelled `BASELINE_GENERIC_PERSON_DETECTOR`; it is not the final survivor model.
