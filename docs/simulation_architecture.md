# Stage 13SIM Phase 1 architecture

Stage 13SIM adds a laboratory around the frozen RescueSwarm autonomy. It does not replace planning, MAVLink routing, geolocation, orchestration, safety, or fleet coordination.

The closed loop is `Gazebo physics → Gazebo IMU/position JSON → official ArduPilot Gazebo plugin → ArduCopter SITL → MAVLink estimates → RescueSwarm`. Commands use the existing `SITLAutopilotAdapter` and `SITLSafetyGuard` and accept loopback SITL only.

`SimulationManager` validates immutable profiles, generates SDF, owns Gazebo/SITL processes, verifies the camera topic and SYSID, and writes run artifacts. It never computes flight commands. The browser receives estimated MAVLink state for normal mission display; perfect Gazebo state is observer-only.

Runtime assets are local. No ROS, cloud service, Fuel download, CDN, distance-triggered detection, or ground-truth target injection exists. Phase 1 deliberately does not connect camera frames to the detector.

Hardware Gate H1 remains **PENDING**.
