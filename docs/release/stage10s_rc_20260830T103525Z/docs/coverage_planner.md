# Coverage planner

Stage 3 turns a validated WGS84 boundary into a deterministic, controller-neutral `CoveragePlan`. The planner reuses the Stage 2 topocentric `LocalCoordinateFrame`; all buffer, intersection, routing, distance, and coverage calculations occur in local east/north metres.

## Planning pipeline

1. Convert the exterior WGS84 ring into mission ENU.
2. Apply the configured inward `edge_margin_m` buffer. An empty or disconnected inset is an error.
3. Generate sweep candidates from polygon edges, the minimum rotated rectangle, and cardinal axes. `FIXED_ANGLE` evaluates only the requested angle.
4. Intersect equally spaced parallel lines with the safe polygon.
5. Alternate pass direction. A concave polygon may produce several pieces on one sweep.
6. Join pieces only with a connector covered by the safe polygon. If the direct connector exits it, a visibility graph over polygon vertices provides the shortest internal connector.
7. Remove adjacent duplicates and genuinely redundant collinear connector points.
8. Reject candidates whose segments leave the inset, exceed the waypoint limit, contain invalid values, or miss the configured coverage threshold.
9. Select the lowest valid cost: search distance + transit distance + turn penalty.
10. Convert the selected path back to WGS84 and persist its canonical fingerprint.

The Stage 3 swath is an engineering approximation. It is configured independently from lane spacing and is not an OV9281 field-of-view claim. Stage 4/5 camera measurements must replace the test defaults.

## Concavity and exclusions

Concave exterior rings are supported and internal connector safety is tested. Stage 2 intentionally accepts one exterior polygon ring and rejects GeoJSON/KML holes. Therefore Stage 3 does not claim exclusion-ring support. A future importer must preserve holes canonically before the planner can safely treat them as no-fly exclusions.

`CoveragePlan.validation` records covered and missed safe area. Search lines are buffered by half the effective swath, unioned, intersected with the safe polygon, and divided by safe area. The default 98% threshold is a planner-quality test, not a competition rule.
