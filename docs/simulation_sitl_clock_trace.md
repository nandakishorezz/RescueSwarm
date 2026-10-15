# Simulation SITL Clock Trace

## Phase-2C: Direct Simulation Clock Mapping

Traces the simulation clock from Gazebo physics to ArduPilot telemetry.

## 1. Gazebo Physics Clock

Source: gz-sim PhysicsSystem / ArduPilotPlugin.cc
_info.simTime = steady_clock nanoseconds since simulation epoch. NOT host wall clock.

## 2. JSON FDM Packet

JSON field: timestamp = simTime.count() / 1e9 (seconds as float)

## 3. SIM_JSON recv_fdm

time_now_us += deltat * 1.0e6  (accumulates Gazebo physics deltat values)
MAVLink time_boot_ms derived from AP_HAL::millis() based on time_now_us.

## 4. Camera Timestamp

gz.msgs.Image.header.stamp from same Gazebo physics clock as _info.simTime.
sim_capture_time_ns = header.sec * 1_000_000_000 + header.nsec

## 5. Phase-2C Clock Chain

With no_time_sync=0 and lock_step=1:
  Gazebo simTime -> JSON timestamp -> SIM_JSON time_now_us
  -> AP_HAL millis -> MAVLink time_boot_ms -> PoseHistory

  gz.msgs.Image.header.stamp (same Gazebo epoch)
  -> DirectSimClockWitness (observe_gazebo_frame + observe_ap_boot)
  -> AffineClockMapping: GAZEBO_SIM -> ARDUPILOT_BOOT
     T_ap = slope * T_gazebo + intercept  (measured, not hardcoded)
  -> DirectSimCaptureClockMapper.map_gazebo_to_autopilot
  -> PoseHistory.lookup -> InterpolatedPose -> Geolocation

## 6. Why no_time_sync=1 Was Wrong

no_time_sync=1 caused SIM_JSON.cpp to set AHRS_EKF_TYPE=10 (SIM AHRS),
overriding the parm file. With no_time_sync=0, EKF3 (type 3) is used.

## 7. Clock Quality States

UNINITIALIZED: no samples
CONVERGING:    samples < 30 OR sim span < 5s
VALID:         samples >= 30 AND span >= 5s AND P95 <= 260ms
DEGRADED:      P95 > max_error_ns
INVALID:       sample age > 120s OR clock reset

max_error_ns=260ms: one MAVLink ATTITUDE interval at 4Hz (telemetry rate bound).
max_age_ns=120s: mapping survives RTL/landing after camera stops.

## 8. Host Wall Clock Role

Used for: sample eviction (120s window), staleness gate (2s), diagnostics.
NOT in evidence chain: no host transport in T_ap = f(T_gazebo) fitting.

## 9. Measured Result (run stage13sim_phase2c_clock_qual_003)

slope:          0.99978 (expected 1.0 under lockstep)
residual_p95:   47.5 ms (within 260ms bound)
sample_count:   600 (buffer full)
fit_outliers:   0
quality:        VALID
autonomy_mapping_permitted: true