# Simulation Confirmation Timing

Status: strict source-time path integrated; confirmation remains blocked behind
clock qualification as required by Phase-2B execution order.

The 63 matched detections from the earlier corrected-label run did not confirm
because all 66 raw detections were rejected before Stage-5 observation creation:
CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED. This is not evidence that the
three-observation threshold is too strict. No detector/confirmation threshold
was changed in Phase 2B.

Existing TargetObservation stores capture timestamps but defaults created_at to
processing wall time. Existing spatial tracking uses created_at for temporal
association/confirmation. That behavior needs a deliberate capture-time upgrade
once mapping qualifies, with replay-speed invariance, distinct-frame evidence,
bounded observation windows and inlier-based confirmation tests.

The new strict mapper path does not bypass this requirement: no observation is
created until a valid Gazebo-capture-to-ArduPilot mapping and pose bracket are
available. The remaining confirmation timing change must use the canonical
sensor/capture timeline, not detector completion wall time, and must be tested
with the same recorded simulation timestamps at different replay speeds.

Do not repurpose a simulation timestamp as UTC without an explicit mission time
contract. Do not count evaluator truth matches as candidate evidence. World
association remains based on estimated location, time and uncertainty only.

Clock fitting buffers are bounded and a synthetic 600-second clock sample
sequence is tested. That test is NOT a ten-minute whole-pipeline memory soak.
Candidate/event/evaluator histories and full memory qualification remain pending.
No single-survivor confirmation or three-survivor gold was attempted after the
failed clock qualification, and no new confirmed-location proof is claimed.
