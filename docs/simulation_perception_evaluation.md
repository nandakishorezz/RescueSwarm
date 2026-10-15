# Simulation Perception Evaluation

The evaluator runs after inference; it does not remove detections, adjust
confidence, inject coordinates, or change the flight. `WorldTruthImageBBox`
is distinct from the normal detection type.

## Segmentation Contract

Harmonic semantic `labels_map` uses replicated grayscale RGB labels. Thus
`[101,101,101]` means label 101, not packed integer 6645093. Recovery fixed the
incorrect 24-bit decoder and verified it with the live static camera. Colored
instance maps are rejected rather than guessed. The runner explicitly supplies
the configured survivor label set to the evaluator. Label zero is background;
the largest visible object is not assumed to be background. One visible pixel
is sufficient for visibility, independent of detector success.

The normal and segmentation sensors share a camera link, profile, resolution,
FOV, update rate and near/far clipping. Existing tests check numeric optics;
full frontend/backend/Gazebo live transform agreement remains unqualified.

## Matching Policy

- Person class id 0 and name `person` are required.
- The live runner uses fixed IoU threshold 0.30.
- Candidate pairs sort by descending IoU, then ascending detection/truth index.
- Greedy one-to-one assignment; no per-frame threshold adjustment.
- Frames pair by nearest sensor simulation timestamp within 100 ms. Pairing
  delta and unpaired counts are persisted. This tolerance is not exact-frame
  synchronization, and must be qualified before strong metric claims.
- Target association uses the exact detection linked to each inlier observation,
  never every detection that happened to share that frame.
- Only the latest estimate for each unique target is scored. Missing identity
  links are unassociated rather than guessed from proximity.

TP/FP/FN and recall currently describe inferred, paired frames, not all generated
frames or complete mission coverage. These are tiny-sample diagnostics, not
statistically meaningful model accuracy. Visibility timings are conditional on
the collected search-phase stream, which starts after takeoff/movement.

## Invalid Historical Evidence

Previous attempt 3's `passed: true`, target errors and confidence in uncertainty
containment cannot qualify Phase 2: capture time was a receipt proxy, label
decoding was wrong, and target association could borrow another detection.
The recovery retry before the label fix also has invalid visibility/FP metrics.
Those directories are preserved unchanged. The authoritative corrected pixel
comparison is `stage13sim_phase2_recovery_labels_20260906`.

All three survivors in that run are `DETECTED_BUT_NOT_CONFIRMED`; 63 TP, 3 FP,
438 FN, precision 0.954545 and conditional recall 0.125749. No qualified
geolocation exists because the timestamp gate rejects every observation.
