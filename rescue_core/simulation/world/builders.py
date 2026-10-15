"""Deterministic local SDF generation from Stage 13SIM profiles."""

from __future__ import annotations

import math
from xml.etree import ElementTree as ET

from ground.backend.app.domain.mission import GeoPoint

from ..georef import SimulationGeoreference
from ..profiles import CameraProfile, DroneProfile, MissionWorldProfile, RotationDirection, SurvivorScenario, WindProfile


def _text(parent: ET.Element, tag: str, value: object, **attributes: str) -> ET.Element:
    node = ET.SubElement(parent, tag, attributes)
    node.text = str(value)
    return node


def _plugin(parent: ET.Element, filename: str, name: str) -> ET.Element:
    return ET.SubElement(parent, "plugin", {"filename": filename, "name": name})


def _xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return "<?xml version='1.0'?>\n" + ET.tostring(root, encoding="unicode") + "\n"


def _quaternion_to_euler(value: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Scalar-first quaternion to intrinsic roll/pitch/yaw for SDF pose."""
    w, x, y, z = value
    roll = math.atan2(2 * (w*x + y*z), 1 - 2 * (x*x + y*y))
    sine = max(-1.0, min(1.0, 2 * (w*y - z*x)))
    pitch = math.asin(sine)
    yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
    return roll, pitch, yaw


def _camera_link_euler(camera_to_body_frd: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Map Stage-5 camera optical axes into a Gazebo camera link.

    Stage 5 optical: +X right, +Y down, +Z forward.
    Gazebo camera link: +X forward, +Y left, +Z up.
    Vehicle profile body is FRD while the SDF body is FLU.
    """
    w, x, y, z = camera_to_body_frd
    r = (
        (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
        (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
        (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)),
    )
    optical_from_link = ((0.0, -1.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0))
    frd_from_link = tuple(tuple(sum(r[i][k]*optical_from_link[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    flu_from_link = (frd_from_link[0], tuple(-v for v in frd_from_link[1]), tuple(-v for v in frd_from_link[2]))
    pitch = math.asin(max(-1.0, min(1.0, -flu_from_link[2][0])))
    if abs(math.cos(pitch)) < 1e-9:
        roll = 0.0; yaw = math.atan2(-flu_from_link[0][1], flu_from_link[1][1])
    else:
        roll = math.atan2(flu_from_link[2][1], flu_from_link[2][2])
        yaw = math.atan2(flu_from_link[1][0], flu_from_link[0][0])
    return roll, pitch, yaw


class GazeboVehicleModelBuilder:
    """Generate collision, inertial, rotor, sensor, and ArduPilot assets.

    Physics geometry always uses profile metres.  UI icon scaling never enters
    this builder.
    """

    def build(
        self, drone: DroneProfile, camera: CameraProfile, *, fdm_port: int = 9002,
        payload_slots: int = 0, vehicle_id: str | None = None,
        include_camera: bool = True,
    ) -> str:
        vehicle_id = vehicle_id or drone.profile_id
        sdf = ET.Element("sdf", {"version": "1.9"})
        model = ET.SubElement(sdf, "model", {"name": vehicle_id})
        _text(model, "self_collide", "false")
        # Follow the official ardupilot_gazebo composition contract: the
        # ArduPilot / aerodynamic systems live on an outer wrapper and address
        # a scoped nested physical airframe.  A direct-link model can load but
        # produces controller-axis instability with this plugin revision.
        airframe = ET.SubElement(model, "model", {"name": "airframe"})
        base = ET.SubElement(airframe, "link", {"name": "base_link"})
        _text(base, "enable_wind", "true")
        velocity_decay = ET.SubElement(base, "velocity_decay")
        _text(velocity_decay, "linear", "0")
        _text(velocity_decay, "angular", "0")
        inertial = ET.SubElement(base, "inertial")
        _text(inertial, "mass", drone.mass_kg.value)
        inertia = ET.SubElement(inertial, "inertia")
        for key in ("ixx", "iyy", "izz", "ixy", "ixz", "iyz"):
            _text(inertia, key, getattr(drone.inertia, key).value)
        for kind in ("collision", "visual"):
            shape = ET.SubElement(base, kind, {"name": f"body_{kind}"})
            geometry = ET.SubElement(shape, "geometry")
            box = ET.SubElement(geometry, "box")
            _text(box, "size", f"{drone.body_length_m.value} {drone.body_width_m.value} {drone.body_height_m.value}")
            if kind == "collision":
                surface = ET.SubElement(shape, "surface")
                friction = ET.SubElement(surface, "friction")
                ode = ET.SubElement(friction, "ode")
                _text(ode, "mu", "1.0")
                _text(ode, "mu2", "1.0")
            if kind == "visual":
                material = ET.SubElement(shape, "material")
                _text(material, "diffuse", "0.05 0.22 0.19 1")
                _text(material, "ambient", "0.03 0.12 0.11 1")
                _text(shape, "cast_shadows", "false")
        imu = ET.SubElement(base, "sensor", {"name": "imu_sensor", "type": "imu"})
        # The official Iris airframe IMU is mounted with a 180-degree roll in
        # Gazebo.  ArduPilotPlugin's model/body transforms assume this sensor
        # convention; an identity IMU reverses controller angular-rate signs.
        _text(imu, "pose", f"0 0 0 {math.pi} 0 0")
        _text(imu, "always_on", "true")
        _text(imu, "update_rate", "1000")
        # No custom IMU topic: ArduPilotPlugin derives and subscribes to the
        # sensor's fully-scoped Gazebo topic. Overriding it would disconnect the
        # publisher from that scoped subscription.

        if include_camera:
            # BODY_FRD translation -> Gazebo model FLU is one centralized mapping:
            # x stays forward, y and z change sign.
            tx, ty, tz = camera.mount_translation_body_frd.value
            camera_link = ET.SubElement(airframe, "link", {"name": "camera_link"})
            roll, pitch, yaw = _camera_link_euler(camera.mount_rotation_camera_to_body.value)
            _text(camera_link, "pose", f"{tx} {-ty} {-tz} {roll} {pitch} {yaw}")
            camera_inertial = ET.SubElement(camera_link, "inertial")
            _text(camera_inertial, "mass", "0.01")
            camera_inertia = ET.SubElement(camera_inertial, "inertia")
            for key, value in (("ixx", 1e-5), ("iyy", 1e-5), ("izz", 1e-5), ("ixy", 0), ("ixz", 0), ("iyz", 0)):
                _text(camera_inertia, key, value)
            fixed = ET.SubElement(airframe, "joint", {"name": "camera_mount_joint", "type": "fixed"})
            _text(fixed, "parent", "base_link")
            _text(fixed, "child", "camera_link")
            sensor = ET.SubElement(camera_link, "sensor", {"name": camera.camera_id, "type": "camera"})
            _text(sensor, "topic", f"/{vehicle_id}/{camera.camera_id}/image")
            _text(sensor, "always_on", "true")
            _text(sensor, "update_rate", camera.fps.value)
            camera_sdf = ET.SubElement(sensor, "camera")
            hfov, _ = camera.derived_fov()
            _text(camera_sdf, "horizontal_fov", f"{hfov:.12f}")
            image = ET.SubElement(camera_sdf, "image")
            _text(image, "width", camera.resolution_width)
            _text(image, "height", camera.resolution_height)
            _text(image, "format", "L_INT8")
            clip = ET.SubElement(camera_sdf, "clip")
            _text(clip, "near", "0.05")
            _text(clip, "far", "500")
            # Evaluator-only matched optical channel.  Its topic is never consumed
            # by CameraSource, DetectorBackend, or autonomy APIs.
            segmentation = ET.SubElement(camera_link, "sensor", {"name": f"{camera.camera_id}_evaluator_segmentation", "type": "segmentation_camera"})
            _text(segmentation, "topic", f"/{vehicle_id}/{camera.camera_id}/evaluator_segmentation")
            _text(segmentation, "always_on", "true")
            _text(segmentation, "update_rate", camera.fps.value)
            segmentation_camera = ET.SubElement(segmentation, "camera")
            # Unique evaluator-only Label-system IDs make every survivor separable
            # in the semantic mask without exposing identity to the autonomy image.
            _text(segmentation_camera, "segmentation_type", "semantic")
            _text(segmentation_camera, "horizontal_fov", f"{hfov:.12f}")
            segmentation_image = ET.SubElement(segmentation_camera, "image")
            _text(segmentation_image, "width", camera.resolution_width)
            _text(segmentation_image, "height", camera.resolution_height)
            segmentation_clip = ET.SubElement(segmentation_camera, "clip")
            _text(segmentation_clip, "near", "0.05")
            _text(segmentation_clip, "far", "500")

        for index, rotor in enumerate(drone.rotors):
            x, y_frd, z_frd = rotor.position_body_frd.value
            link = ET.SubElement(airframe, "link", {"name": f"rotor_{index}"})
            _text(link, "pose", f"{x} {-y_frd} {-z_frd} 0 0 0")
            _text(link, "enable_wind", "true")
            rotor_inertial = ET.SubElement(link, "inertial")
            # Reference propeller inertial/damping values follow the official
            # ardupilot_gazebo Iris dynamics. They remain REFERENCE_PRESET,
            # never measured RescueSwarm hardware evidence.
            _text(rotor_inertial, "mass", "0.025")
            rotor_tensor = ET.SubElement(rotor_inertial, "inertia")
            for key, value in (("ixx", 9.75e-6), ("iyy", 0.000166704), ("izz", 0.000167604), ("ixy", 0), ("ixz", 0), ("iyz", 0)):
                _text(rotor_tensor, key, value)
            collision = ET.SubElement(link, "collision", {"name": f"rotor_{index}_collision"})
            collision_geometry = ET.SubElement(collision, "geometry")
            collision_cylinder = ET.SubElement(collision_geometry, "cylinder")
            _text(collision_cylinder, "radius", rotor.diameter.value / 2)
            _text(collision_cylinder, "length", "0.005")
            visual = ET.SubElement(link, "visual", {"name": f"rotor_{index}_visual"})
            geometry = ET.SubElement(visual, "geometry")
            cylinder = ET.SubElement(geometry, "cylinder")
            _text(cylinder, "radius", rotor.diameter.value / 2)
            _text(cylinder, "length", "0.008")
            material = ET.SubElement(visual, "material")
            _text(material, "diffuse", "0.15 0.18 0.18 0.72")
            _text(visual, "cast_shadows", "false")
            joint = ET.SubElement(airframe, "joint", {"name": f"rotor_{index}_joint", "type": "revolute"})
            _text(joint, "parent", "base_link")
            _text(joint, "child", f"rotor_{index}")
            axis = ET.SubElement(joint, "axis")
            _text(axis, "xyz", "0 0 1")
            dynamics = ET.SubElement(axis, "dynamics")
            _text(dynamics, "damping", "0.004")
            limit = ET.SubElement(axis, "limit")
            _text(limit, "lower", "-1e16")
            _text(limit, "upper", "1e16")
            _text(limit, "effort", "100")
            _text(limit, "velocity", rotor.thrust_model.max_rotor_velocity.value)
            # Two aerodynamic control points on a physically rotating rotor.
            # This follows the official ardupilot_gazebo Iris mechanism: rotor
            # joint speed creates tangential airspeed, LiftDrag creates force,
            # and Gazebo rigid-body physics produces vehicle motion.
            cp = rotor.diameter.value * 0.33
            blade_area = max(0.0001, rotor.diameter.value * rotor.diameter.value * 0.031)
            direction_sign = -1 if rotor.rotation_direction == RotationDirection.CW else 1
            for side in (1, -1):
                lift = _plugin(model, "gz-sim-lift-drag-system", "gz::sim::systems::LiftDrag")
                _text(lift, "link_name", f"airframe::rotor_{index}")
                _text(lift, "a0", "0.3")
                _text(lift, "alpha_stall", "1.4")
                _text(lift, "cla", "4.25")
                _text(lift, "cda", "0.1")
                _text(lift, "cma", "0")
                _text(lift, "cla_stall", "-0.025")
                _text(lift, "cda_stall", "0")
                _text(lift, "cma_stall", "0")
                _text(lift, "area", blade_area)
                _text(lift, "air_density", "1.2041")
                _text(lift, "cp", f"{side * cp} 0 0")
                _text(lift, "forward", f"0 {side * direction_sign} 0")
                _text(lift, "upward", "0 0 1")
            apply_force = _plugin(model, "gz-sim-apply-joint-force-system", "gz::sim::systems::ApplyJointForce")
            _text(apply_force, "joint_name", f"airframe::rotor_{index}_joint")

        plugin = _plugin(model, "ArduPilotPlugin", "ArduPilotPlugin")
        _text(plugin, "fdm_addr", "127.0.0.1")
        _text(plugin, "fdm_port_in", fdm_port)
        _text(plugin, "connectionTimeoutMaxCount", "5")
        _text(plugin, "lock_step", "1")
        # Phase-2C: no_time_sync=0 allows EKF3.
        # With no_time_sync=1, SIM_JSON.cpp line 413 forces AHRS_EKF_TYPE=10
        # (SIM AHRS), which disqualifies gold pose estimation.
        # With no_time_sync=0 and lock_step=1, ArduPilot receives the Gazebo
        # physics timestamp via the JSON "timestamp" field and drives
        # time_now_us from it — the direct simulation clock relationship.
        _text(plugin, "no_time_sync", "0")
        _text(plugin, "have_32_channels", "0")
        _text(plugin, "modelXYZToAirplaneXForwardZDown", "0 0 0 3.141592653589793 0 0")
        _text(plugin, "gazeboXYZToNED", "0 0 0 3.141592653589793 0 1.570796326794897")
        # Unscoped name is robust for a generated top-level model; the official
        # plugin resolves it within this model and verifies it is an IMU entity.
        _text(plugin, "imuName", "airframe::base_link::imu_sensor")
        for index, rotor in enumerate(drone.rotors):
            control = ET.SubElement(plugin, "control", {"channel": str(index)})
            _text(control, "jointName", f"airframe::rotor_{index}_joint")
            _text(control, "useForce", "1")
            sign = -1 if rotor.rotation_direction == RotationDirection.CW else 1
            _text(control, "multiplier", sign * rotor.thrust_model.max_rotor_velocity.value)
            _text(control, "offset", "0")
            _text(control, "servo_min", "1100")
            _text(control, "servo_max", "1900")
            _text(control, "type", "VELOCITY")
            _text(control, "p_gain", "0.20")
            _text(control, "i_gain", "0")
            _text(control, "d_gain", "0")
            _text(control, "i_max", "0")
            _text(control, "i_min", "0")
            _text(control, "cmd_max", "2.5")
            _text(control, "cmd_min", "-2.5")
            _text(control, "controlVelocitySlowdownSim", "1")
        for slot in range(1, payload_slots + 1):
            mount_name = f"payload_mount_{slot}"
            mount = ET.SubElement(model, "link", {"name": mount_name})
            # Move payload to Z=-0.08 to avoid drone's base_link collision box (bottom is -0.055)
            # Offset by slot to avoid collision overlap
            payload_x = 0.0
            payload_y = 0.0
            payload_z = -0.08 - (slot - 1) * 0.03
            _text(mount, "pose", f"{payload_x} {payload_y} {payload_z:.3f} 0 0 1.571")
            mount_inertial = ET.SubElement(mount, "inertial")
            _text(mount_inertial, "mass", "0.01")
            mount_tensor = ET.SubElement(mount_inertial, "inertia")
            for key, value in (("ixx", 1e-5), ("iyy", 1e-5), ("izz", 1e-5), ("ixy", 0), ("ixz", 0), ("iyz", 0)):
                _text(mount_tensor, key, value)
            mount_joint = ET.SubElement(model, "joint", {"name": f"payload_mount_joint_{slot}", "type": "fixed"})
            _text(mount_joint, "parent", "airframe::base_link")
            _text(mount_joint, "child", mount_name)
            detachable = _plugin(
                model, "gz-sim-detachable-joint-system",
                "gz::sim::systems::DetachableJoint",
            )
            _text(detachable, "parent_link", mount_name)
            _text(detachable, "child_model", f"payload_slot_{slot}")
            _text(detachable, "child_link", "body")
            _text(detachable, "detach_topic", f"/rescueswarm/payload/slot_{slot}/detach")
            _text(detachable, "attach_topic", f"/rescueswarm/payload/slot_{slot}/attach")
            _text(detachable, "output_topic", f"/rescueswarm/payload/slot_{slot}/state")
        pose_publisher = _plugin(model, "gz-sim-pose-publisher-system", "gz::sim::systems::PosePublisher")
        _text(pose_publisher, "publish_model_pose", "true")
        _text(pose_publisher, "publish_nested_model_pose", "true")
        _text(pose_publisher, "publish_link_pose", "true")
        _text(pose_publisher, "publish_sensor_pose", "false")
        _text(pose_publisher, "publish_collision_pose", "false")
        _text(pose_publisher, "publish_visual_pose", "false")
        _text(pose_publisher, "use_pose_vector_msg", "true")
        _text(pose_publisher, "update_frequency", "10")
        return _xml(sdf)


class WorldBuilder:
    def build(
        self,
        mission: MissionWorldProfile,
        drone: DroneProfile,
        wind: WindProfile,
        survivors: SurvivorScenario,
        *,
        payload_slots: int = 0,
        fleet_count: int = 1,
        fleet_spawn_offset_m: float = 2.0,
    ) -> str:
        sdf = ET.Element("sdf", {"version": "1.9"})
        world = ET.SubElement(sdf, "world", {"name": "rescueswarm_stage13sim"})
        _text(world, "gravity", "0 0 -9.80665")
        _text(world, "magnetic_field", "2.15e-5 5.5e-6 -4.4e-5")
        physics = ET.SubElement(world, "physics", {"name": "high_fidelity_1khz", "type": "ignored"})
        _text(physics, "max_step_size", "0.001")
        _text(physics, "real_time_factor", "1.0")
        _plugin(world, "gz-sim-physics-system", "gz::sim::systems::Physics")
        _plugin(world, "gz-sim-user-commands-system", "gz::sim::systems::UserCommands")
        _plugin(world, "gz-sim-scene-broadcaster-system", "gz::sim::systems::SceneBroadcaster")
        sensors = _plugin(world, "gz-sim-sensors-system", "gz::sim::systems::Sensors")
        _text(sensors, "render_engine", "ogre2")
        _plugin(world, "gz-sim-imu-system", "gz::sim::systems::Imu")
        _plugin(world, "gz-sim-navsat-system", "gz::sim::systems::NavSat")
        wind_system = _plugin(world, "gz-sim-wind-effects-system", "gz::sim::systems::WindEffects")
        # WindEffects converts this world wind into link forces for links with
        # enable_wind=true.  This value is not sent to RescueSwarm autonomy.
        _text(wind_system, "force_approximation_scaling_factor", "1")
        if wind.enabled and wind.mode.value in {"TURBULENT", "GUSTING"}:
            horizontal = ET.SubElement(wind_system, "horizontal")
            magnitude = ET.SubElement(horizontal, "magnitude")
            _text(magnitude, "time_for_rise", "1")
            if wind.gusts_enabled and wind.mean_speed_mps.value > 0:
                sine = ET.SubElement(magnitude, "sin")
                _text(sine, "amplitude_percent", max(0.0, wind.gust_max_speed_mps.value / wind.mean_speed_mps.value - 1.0))
                _text(sine, "period", max(2.0, wind.gust_max_duration_s.value * 4.0))
            if wind.turbulence_enabled:
                noise = ET.SubElement(magnitude, "noise", {"type": "gaussian"})
                _text(noise, "mean", "0")
                _text(noise, "stddev", wind.turbulence_intensity.value / max(wind.mean_speed_mps.value, 1.0))
            direction_config = ET.SubElement(horizontal, "direction")
            _text(direction_config, "time_for_rise", "1")
            direction_sine = ET.SubElement(direction_config, "sin")
            _text(direction_sine, "amplitude", math.radians(wind.direction_variation_deg.value))
            _text(direction_sine, "period", max(4.0, wind.gust_max_duration_s.value * 6.0))
        direction = math.radians(wind.mean_direction_deg.value)
        speed = wind.mean_speed_mps.value if wind.enabled else 0.0
        wind_node = ET.SubElement(world, "wind")
        _text(wind_node, "linear_velocity", f"{speed * math.sin(direction):.9f} {speed * math.cos(direction):.9f} {wind.vertical_speed_mps.value:.9f}")
        spherical = ET.SubElement(world, "spherical_coordinates")
        _text(spherical, "latitude_deg", mission.origin_wgs84[0])
        _text(spherical, "longitude_deg", mission.origin_wgs84[1])
        _text(spherical, "elevation", mission.origin_wgs84[2])
        _text(spherical, "heading_deg", "0")
        _text(spherical, "surface_model", "EARTH_WGS84")
        scene = ET.SubElement(world, "scene")
        # The headless OGRE2 camera path used by the WSL simulation was
        # measured at 20.5 DN on the ground: 0.35 scene ambient multiplied by
        # the 0.23 ground material is almost exactly that value.  The explicit
        # directional light does not contribute reliably in this renderer.
        # 0.80 brings the neutral mono background to about 47 DN, matching the
        # untouched V3 positive images (median 48 DN), without post-processing
        # or changing the detector input independently from the browser image.
        _text(scene, "ambient", "0.80 0.80 0.80 1")
        _text(scene, "background", "0.72 0.84 0.92 1")
        _text(scene, "shadows", "true")
        sun = ET.SubElement(world, "light", {"name": "sun", "type": "directional"})
        _text(sun, "pose", "0 0 40 0 0 0")
        _text(sun, "direction", "-0.4 0.2 -0.9")
        _text(sun, "diffuse", "1 1 1 1")
        _text(sun, "specular", "0.1 0.1 0.1 1")
        _text(sun, "cast_shadows", "true")
        ground = ET.SubElement(world, "model", {"name": "flat_ground"})
        _text(ground, "static", "true")
        link = ET.SubElement(ground, "link", {"name": "ground_link"})
        for kind in ("collision", "visual"):
            part = ET.SubElement(link, kind, {"name": f"ground_{kind}"})
            geometry = ET.SubElement(part, "geometry")
            box = ET.SubElement(geometry, "box")
            _text(box, "size", f"{mission.world_dimensions_m.value[0]} {mission.world_dimensions_m.value[1]} 0.1")
            if kind == "visual":
                material = ET.SubElement(part, "material")
                _text(material, "diffuse", "0.40 0.58 0.34 1")
                # The headless OGRE2 sensor renderer does not reliably carry
                # scene ambient into a diffuse-only material.  Give the
                # camera-visible ground an explicit ambient term so the
                # live mono stream contains real scene contrast.
                # The first post-fix live topic still measured 28.3 DN.  The
                # V3 positive-image median is 48 DN, so use the measured 1.7x
                # material ratio at the renderer itself (not a frontend/model
                # gain) to keep browser and inference pixels identical.
                _text(material, "ambient", "0.40 0.58 0.34 1")

        origin = GeoPoint(latitude=mission.origin_wgs84[0], longitude=mission.origin_wgs84[1], altitude_m=mission.origin_wgs84[2])
        georef = SimulationGeoreference(origin)
        boundary = [georef.wgs84_to_gazebo(GeoPoint(latitude=lat, longitude=lon, altitude_m=mission.origin_wgs84[2])) for lat, lon in mission.boundary_wgs84]
        for index, (start, end) in enumerate(zip(boundary, boundary[1:] + boundary[:1])):
            dx, dy = end.x_m - start.x_m, end.y_m - start.y_m
            length = math.hypot(dx, dy)
            marking = ET.SubElement(world, "model", {"name": f"mission_boundary_{index}"})
            _text(marking, "static", "true")
            _text(marking, "pose", f"{(start.x_m + end.x_m)/2} {(start.y_m + end.y_m)/2} 0.015 0 0 {math.atan2(dy, dx)}")
            mark_link = ET.SubElement(marking, "link", {"name": "marking"})
            visual = ET.SubElement(mark_link, "visual", {"name": "visual"})
            geometry = ET.SubElement(visual, "geometry")
            box = ET.SubElement(geometry, "box")
            _text(box, "size", f"{max(length, 0.01)} 0.08 0.02")
            material = ET.SubElement(visual, "material")
            _text(material, "diffuse", "0.1 0.95 0.65 0.8")

        self._launch_zone(world, mission)
        for survivor_index, survivor in enumerate(survivors.survivors, start=1):
            if survivor.enabled:
                point = georef.wgs84_to_gazebo(GeoPoint(latitude=survivor.latitude, longitude=survivor.longitude, altitude_m=mission.origin_wgs84[2] + survivor.up_m))
                self._survivor(
                    world, survivor.survivor_id, point.x_m, point.y_m, point.z_m,
                    survivor.yaw_deg, survivor.body_state, survivor.visual_variant,
                    100 + survivor_index,
                )
        launch_z = 0.206 if payload_slots > 0 else 0.126
        for slot in range(1, payload_slots + 1):
            self._payload(world, slot, payload_slots, launch_z)
        for index in range(1, fleet_count + 1):
            vehicle_id = f"drone_{index:02d}"
            include = ET.SubElement(world, "include")
            _text(include, "uri", f"model://{vehicle_id}")
            _text(include, "name", vehicle_id)
            offset = (index - 1) * fleet_spawn_offset_m
            _text(include, "pose", f"0 {offset} {launch_z:.3f} 0 0 {math.pi / 2}")
        return _xml(sdf)

    @staticmethod
    def _payload(world: ET.Element, slot: int, payload_slots: int, launch_z: float) -> None:
        """Create a carried 200 g reference kit as an independent rigid body."""
        model = ET.SubElement(world, "model", {"name": f"payload_slot_{slot}"})
        # Move payload to Z=-0.08 relative to drone to avoid collision overlap
        payload_x = 0.0
        payload_y = 0.0
            
        payload_z = launch_z - 0.08 - (slot - 1) * 0.03
        _text(model, "pose", f"{payload_x} {payload_y} {payload_z:.3f} 0 0 1.571")
        link = ET.SubElement(model, "link", {"name": "body"})
        _text(link, "enable_wind", "true")
        inertial = ET.SubElement(link, "inertial")
        mass, x, y, z = 0.2, 0.2, 0.1, 0.02
        _text(inertial, "mass", str(mass))
        tensor = ET.SubElement(inertial, "inertia")
        _text(tensor, "ixx", mass * (y*y + z*z) / 12)
        _text(tensor, "iyy", mass * (x*x + z*z) / 12)
        _text(tensor, "izz", mass * (x*x + y*y) / 12)
        for key in ("ixy", "ixz", "iyz"):
            _text(tensor, key, 0)
        for kind in ("collision", "visual"):
            node = ET.SubElement(link, kind, {"name": f"payload_{kind}"})
            if kind == "visual":
                visual_y = 0.25 if slot == 1 else (-0.25 if slot == 2 else 0.0)
                visual_x = -0.25 if slot == 3 else 0.0
                _text(node, "pose", f"{visual_x} {visual_y} 0 0 0 0")
            geometry = ET.SubElement(node, "geometry")
            box = ET.SubElement(geometry, "box")
            if kind == "collision":
                _text(box, "size", f"{x} {y} {z}")
            else:
                _text(box, "size", f"{x} {y} 0.02")
            if kind == "visual":
                material = ET.SubElement(node, "material")
                _text(material, "diffuse", "0.92 0.32 0.05 1")
                _text(material, "ambient", "0.45 0.12 0.02 1")
        publisher = _plugin(model, "gz-sim-pose-publisher-system", "gz::sim::systems::PosePublisher")
        _text(publisher, "publish_model_pose", "true")
        _text(publisher, "publish_link_pose", "false")
        _text(publisher, "use_pose_vector_msg", "true")
        _text(publisher, "update_frequency", "20")

    @staticmethod
    def _launch_zone(world: ET.Element, mission: MissionWorldProfile) -> None:
        width, length = mission.launch_zone_width_m.value, mission.launch_zone_length_m.value
        for index, (x, y, sx, sy) in enumerate(((0, length/2, width, .06), (0, -length/2, width, .06), (width/2, 0, .06, length), (-width/2, 0, .06, length))):
            model = ET.SubElement(world, "model", {"name": f"launch_zone_{index}"})
            _text(model, "static", "true")
            _text(model, "pose", f"{x} {y} 0.02 0 0 0")
            link = ET.SubElement(model, "link", {"name": "marking"})
            visual = ET.SubElement(link, "visual", {"name": "visual"})
            geometry = ET.SubElement(visual, "geometry")
            box = ET.SubElement(geometry, "box")
            _text(box, "size", f"{sx} {sy} 0.02")
            material = ET.SubElement(visual, "material")
            _text(material, "diffuse", "1 0.72 0.15 0.9")

    @staticmethod
    def _survivor(
        world: ET.Element, survivor_id: str, x: float, y: float, z: float,
        yaw_deg: float, body_state: str, visual_variant: str, evaluator_label: int,
    ) -> None:
        model = ET.SubElement(world, "model", {"name": f"survivor_{survivor_id}"})
        _text(model, "static", "true")
        lying = body_state == "lying"
        # CesiumMan's glTF-up convention needs this local roll to stand in
        # Gazebo's Z-up world. Lying is deliberately a hard perception case.
        gazebo_actor = visual_variant.startswith("GAZEBO_ACTOR_")
        asset_roll = math.pi / 2 if lying and gazebo_actor else (0.0 if gazebo_actor or lying else math.pi / 2)
        _text(model, "pose", f"{x} {y} {z + (0.2 if lying else 0)} {asset_roll} 0 {math.radians(yaw_deg)}")
        link = ET.SubElement(model, "link", {"name": "person"})
        label = _plugin(model, "gz-sim-label-system", "gz::sim::systems::Label")
        _text(label, "label", evaluator_label)
        if gazebo_actor:
            pose_mesh = "sitting.dae" if body_state == "sitting" else "stand.dae"
            person = ET.SubElement(link, "visual", {"name": "licensed_gazebo_human_mesh"})
            geometry = ET.SubElement(person, "geometry")
            mesh = ET.SubElement(geometry, "mesh")
            _text(mesh, "uri", f"model://gazebo_actor/meshes/{pose_mesh}")
            _text(mesh, "scale", "1 1 1")
            return
        if visual_variant == "CESIUM_MAN_CC_BY_4_0":
            person = ET.SubElement(link, "visual", {"name": "licensed_human_mesh"})
            geometry = ET.SubElement(person, "geometry")
            mesh = ET.SubElement(geometry, "mesh")
            _text(mesh, "uri", "model://survivors/CesiumMan.glb")
            _text(mesh, "scale", "1 1 1")
            return
        torso = ET.SubElement(link, "visual", {"name": "torso"})
        _text(torso, "pose", "0 0 0.9 0 0 0")
        geometry = ET.SubElement(torso, "geometry")
        cylinder = ET.SubElement(geometry, "cylinder")
        _text(cylinder, "radius", "0.18")
        _text(cylinder, "length", "1.2")
        material = ET.SubElement(torso, "material")
        _text(material, "diffuse", "0.95 0.28 0.05 1")
        _text(material, "ambient", "0.95 0.28 0.05 1")
        head = ET.SubElement(link, "visual", {"name": "head"})
        _text(head, "pose", "0 0 1.62 0 0 0")
        geometry = ET.SubElement(head, "geometry")
        sphere = ET.SubElement(geometry, "sphere")
        _text(sphere, "radius", "0.16")
        material = ET.SubElement(head, "material")
        _text(material, "diffuse", "0.78 0.55 0.38 1")
        _text(material, "ambient", "0.78 0.55 0.38 1")
