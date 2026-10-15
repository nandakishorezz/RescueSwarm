# Phase-2 Truth Firewall

Status: **PASS**. The complete Phase-2E import/API/dataflow/causality audit is
recorded in `GROUND_TRUTH_ACCESS_AUDIT.md`.

## Three Categories

World truth: scenario positions, renderer segmentation, perfect vehicle pose,
wind configuration. Only generation and observer/evaluator code may read it.

Sensor observations: live camera pixels and MAVLink telemetry.

Autonomy estimates: real YOLO boxes and accepted Stage-5/confirmation output.
No Gazebo frame is currently accepted for geolocation until capture timing is
verified. This rejection is independent of survivor truth and preserves all
raw detector results, including false positives.

## Verified Checks

- Hidden survivor movement leaves the independent mission fingerprint unchanged.
- Normal perception API allowlists summary fields and omits hidden coordinates.
- Observer endpoints require explicit opt-in.
- AST import test includes the Phase-2 perception adapter and frozen vision,
  geolocation, planning, fleet, autonomy and SITL packages.
- Evaluator regression proves a false track cannot borrow a survivor detection
  from the same frame.
- Unmapped frame regression proves it cannot call Stage-5 geolocation.
- START resets the observer toggle and cached observer data.

This is architectural separation, not OS sandboxing: evaluator and autonomy
adapters coexist in the CLI runner process, and the same account can read local
files. Tests do not prove arbitrary code is unable to open truth files.

## Phase-2E closure

Evaluator-disabled and truth-overlay fingerprints are identical, the normal
Mission and simulation APIs exclude hidden coordinates, the transitive import
surface is covered, and frame-linked replay uses source-time EKF pose. Legacy
synthetic rescue runners remain excluded from gold qualification.
