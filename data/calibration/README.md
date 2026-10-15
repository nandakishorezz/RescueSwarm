# Camera calibration registry

Each generated calibration is stored under `cameras/<calibration_id>/` with
hashed intrinsics, optional fixed-mount extrinsics, validation evidence, and a
manifest. No physical OV9281 calibration is checked in. Synthetic test
calibrations are generated inside test/run artifacts and must never be selected
as physical evidence.

