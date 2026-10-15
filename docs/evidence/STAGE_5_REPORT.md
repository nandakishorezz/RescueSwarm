# RescueSwarm Stage 5 Report

Generated: 2026-08-29T04:12:54.517707+00:00

## CAMERA CALIBRATION

- Intrinsics software: PASS
- Physical OV9281 intrinsics: NOT_TESTED
- Synthetic calibration ID: stage5_synthetic_pinhole
- Distortion model: PINHOLE_BROWN
- Resolution: 1280x800
- Physical RMS/validation: NOT_MEASURED / NOT_TESTED

## EXTRINSICS

- Synthetic fixture: CALIBRATED (TEST ONLY)
- Physical camera-to-body transform: UNKNOWN / NOT_TESTED

## CLOCK SYNC

- Synthetic offset: 5000000000 ns
- RTT P50/P95: 2.0 / 2.0 ms
- Synthetic quality: SYNCED
- Live SITL: NOT_TESTED
- Physical: NOT_TESTED

## POSE SYNCHRONIZATION

- Bounded history/interpolation/SLERP/stale rejection: PASS

## GEOLOCATION

- Ground model: SYNTHETIC_FLAT_GROUND
- Zero-noise error: 0.0 m
- Noisy median error / P95 spread: 0.14515618005147732 / 4.8354451894765615 m
- Timing offsets: {"0_ms": 0.0, "100_ms": 0.3999999999999999, "200_ms": 0.8}
- Attitude errors: {"roll_0_deg": 0.0, "roll_1_deg": 0.3618634791266194, "roll_2_deg": 0.7214253408870808}
- Extrinsic errors: {"tilt_0_deg": 0.0, "tilt_1_deg": 0.353290694314234, "tilt_2_deg": 0.7080629226790338}
- Common GNSS bias error: 2.0 m
- Ground-height mismatch error: 0.49690399499995447 m

## TARGET CONFIRMATION

- Candidate/non-consecutive confirmation: PASS
- Robust fusion/MAD outlier rejection: PASS

## PHYSICAL ACCURACY

- CAMERA CALIBRATION PHYSICAL: NOT_TESTED
- PHYSICAL GEOLOCATION: NOT_TESTED
- REAL M8N TARGET ACCURACY: NOT_MEASURED

## VERDICT

- MATHEMATICAL GEOLOCATION VERIFIED: PASS
- PHYSICAL GEOLOCATION ACCURACY VERIFIED: NO
