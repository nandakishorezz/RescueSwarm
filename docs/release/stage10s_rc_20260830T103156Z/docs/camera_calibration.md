# Camera intrinsic and mount calibration

Physical OV9281 calibration has not been performed in this repository. Calibration is resolution- and sensor-mode-specific; a mismatch produces `CALIBRATION_MISMATCH` and no coordinate.

Generate a checkerboard, print at 100% scale, and physically measure several squares:

```bash
python tools/generate_calibration_target.py --columns 9 --rows 6 \
  --square-size-mm 25 --output calibration/checkerboard_9x6.svg
```

The nominal SVG size is not proof that the printer reproduced it correctly. Pass the measured square size to calibration.

On the Pi bench, after Stage 4 camera enumeration and mode discovery pass:

```bash
python tools/capture_calibration_dataset.py calibration/datasets/ov9281_1280x800 \
  --width 1280 --height 800 --fps 10 --count 30 --interval 1
```

Move the pattern through the image center, corners and edges at different distances, rotations, and tilts. The capture tool rejects gross blur. Calibration additionally rejects unreadable frames, incomplete patterns, resolution mismatches, and near-duplicate pattern coverage.

Fit both justified lens models when useful:

```bash
python tools/calibrate_camera.py calibration/datasets/ov9281_1280x800 \
  --calibration-id ov9281_pinhole_1280x800_v1 --sensor-mode MODE_FROM_DISCOVERY \
  --columns 9 --rows 6 --square-size-mm MEASURED_VALUE --model PINHOLE_BROWN

python tools/calibrate_camera.py calibration/datasets/ov9281_1280x800 \
  --calibration-id ov9281_fisheye_1280x800_v1 --sensor-mode MODE_FROM_DISCOVERY \
  --columns 9 --rows 6 --square-size-mm MEASURED_VALUE --model FISHEYE
```

Use separate held-out images:

```bash
python tools/validate_camera_calibration.py ov9281_pinhole_1280x800_v1 calibration/validation_images
python tools/validate_camera_calibration.py ov9281_fisheye_1280x800_v1 calibration/validation_images
python tools/compare_camera_calibrations.py \
  calibration/cameras/ov9281_pinhole_1280x800_v1/held_out_validation/validation.json \
  calibration/cameras/ov9281_fisheye_1280x800_v1/held_out_validation/validation.json
```

Review RMS, mean, median, P95, every frame error, undistorted held-out previews, and rejection reasons. Thresholds are engineering configuration, not universal truth.

Camera-to-body extrinsics are a separate artifact. `tools/create_camera_extrinsics.py` accepts body-FRD translation and an explicitly characterized camera-to-body Euler rotation. `APPROXIMATE_NADIR` is permitted for development but must remain `APPROXIMATE`; accurate physical localization requires a measured/calibrated transform. No physical mount transform is checked in.

