# Stage 6 evidence and artifacts

Each immutable hardware run is stored under `data/hardware_runs/<run_id>/`. The baseline includes system, power, serial, boot, camera, kernel, network, MAVLink, firmware, parameter hash, GPS, compass, EKF, and report files. Missing inputs are serialized as `NOT_TESTED`; they are never omitted or converted to success.

Generated `config/local_hardware.yaml` and `config/local_vision.yaml` link operational choices to evidence but are ignored by Git. `config/prototype.yaml` remains the configured prototype baseline.

Controlled physical imagery belongs under `data/physical_vision/<session>/` with `images/`, frame/detection NDJSON, calibration and extrinsics IDs, scene notes, and target truth. Collect only necessary images and no unnecessary personal data.

Status vocabulary is `VERIFIED`, `FAILED`, `PARTIAL`, `UNKNOWN`, `NOT_TESTED`, and `BLOCKED`. A camera label photo, MAVLink board metadata, and configured model are distinct evidence. GPS repeatability and absolute error are distinct metrics.
