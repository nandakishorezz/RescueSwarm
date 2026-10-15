# Observation association and target confirmation

A single detection creates only a candidate. `SpatiotemporalTargetTracker` uses geolocated ENU distance, uncertainty overlap, and time as its primary association. It does not require consecutive frames. Confirmed tracks are retained longer to suppress duplicate survivor creation on adjacent lanes or later passes.

`TargetFusionEngine` first finds a coordinate-wise spatial median, applies a median/MAD distance gate, then computes an inverse-geographic-uncertainty weighted position. Detector confidence only provides a small bounded weight adjustment. Outlier and inlier observation IDs, calibration IDs, extrinsics IDs, evidence paths, counts and time ranges remain auditable.

Fused uncertainty includes residual spread and a non-reducible common-bias floor. The deterministic +2 m east GNSS-bias experiment correctly remains approximately +2 m after multiple observations.

Stage 5 does not implement appearance ReID, ByteTrack, target approach, mission interruption, or payload delivery.

