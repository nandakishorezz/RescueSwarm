"""Live Stage 13SIM Phase-2 pixel-only perception qualification.

The flight and mission plan are generated without survivor inputs.  Hidden
truth is opened only after the live vision pipeline has stopped, for evaluator
metrics and never for inference, confirmation, or geolocation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import math
import socket
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from pymavlink import mavutil
from shapely.geometry import Polygon

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder
from ground.backend.app.sitl.payload import GazeboPayloadAdapter
from ground.backend.app.sitl.phase4_service import Phase4TargetServiceController
from ground.backend.app.sitl.pose import MAVLinkPoseSubscriber
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.payload import PayloadInventory
from rescue_core.geolocation.clock_sync import ClockSyncEstimator
from rescue_core.simulation.gazebo import GazeboTruthObserver, WslGazeboLauncher
from rescue_core.simulation.perception.clock import (
    DirectSimClockWitness, DirectSimCaptureClockMapper, SimulationClockMapper,
    SimulationPoseTimestampWitness, SimulationPoseTimestampWitnessRecord,
)
from rescue_core.simulation.artifacts import SimulationArtifactWriter
from rescue_core.simulation.evaluator import GazeboSegmentationSource, PerceptionEvaluator, compact_visibility
from rescue_core.simulation.georef import SimulationGeoreference
from rescue_core.simulation.orchestration import SimulationManager, SimulationRunDefinition
from rescue_core.simulation.perception import (
    GazeboCameraSource, PixelPerceptionPipeline, SensorRealismCameraSource,
)
from rescue_core.simulation.perception.camera_source import _free_loopback_port, _wsl_path
from rescue_core.simulation.profiles import (
    MissionWorldProfile,
    ScalarValue,
    SimulationConfig,
    SurvivorPlacement,
    SurvivorScenario,
    Vector3Value,
    WindMode,
    load_camera_profile,
    load_drone_profile,
    load_environment_profile,
)
from rescue_core.simulation.sensor_realism import (
    SensorSeeds, get_isolated_sensor_profile, get_sensor_realism_profile,
)
from rescue_core.geolocation.clock_sync import MappingQuality
from rescue_core.vision.config import load_vision_config
from rescue_core.vision.factory import create_detector
from rescue_core.vision.models import detection_result_from_dict

ROOT = Path(__file__).resolve().parents[1]

# Historical fail-closed markers retained for Phase-2C evidence compatibility;
# the separate Phase-2E aggregate runner owns their closure evidence.
HISTORICAL_PHASE2C_BLOCKERS_CLOSED_BY_PHASE2E = (
    "LIVE_FOV_OCCLUSION_AND_WIND_PIXEL_CONTROLS_NOT_QUALIFIED",
    "ANGLE_ALTITUDE_SPEED_EXPERIMENTS_NOT_COMPLETE",
)


def build_definition(
    run_id: str,
    *,
    speed_mps: float = 3.0,
    altitude_m: float = 8.0,
    wind_profile: str = "PERCEPTION_MODERATE",
    camera_profile_path: Path | None = None,
    survivor_count: int = 3,
    sensor_profile: str = "IDEAL_CONTROL",
    sensor_seed: int = 31003,
    sensor_component: str | None = None,
    payload_slots: int = 0,
) -> SimulationRunDefinition:
    drone = load_drone_profile(ROOT / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
    camera = load_camera_profile(camera_profile_path or ROOT / "simulation" / "profiles" / "ov9281_phase2_oblique_simulation.yaml")
    environment = load_environment_profile(ROOT / "simulation" / "profiles" / "perception_moderate_environment.yaml")
    if wind_profile == "PERCEPTION_CALM":
        wind = environment.wind.model_copy(update={
            "enabled": False,
            "mode": WindMode.CALM,
            "turbulence_enabled": False,
            "gusts_enabled": False,
            "mean_speed_mps": environment.wind.mean_speed_mps.model_copy(update={"value": 0.0}),
        })
        environment = environment.model_copy(update={"profile_id": "perception_calm_seed_84521", "wind": wind})
    drone = drone.__class__.model_validate({
        **drone.model_dump(mode="json"),
        "cruise_speed_mps": {**drone.cruise_speed_mps.model_dump(mode="json"), "value": speed_mps},
    })
    boundary = BoundaryService(10).import_boundary(
        "small_easy.geojson", (ROOT / "data" / "fixtures" / "stage10s" / "small_easy.geojson").read_bytes(),
    )
    assert boundary.origin is not None
    origin = GeoPoint(latitude=boundary.origin.latitude, longitude=boundary.origin.longitude, altitude_m=585)
    georef = SimulationGeoreference(origin)
    # Fixed independent scenario coordinates lie under normal Stage-3 lanes;
    # neither planner nor mission builder receives this collection.
    placements = (
        (17.5, -3.0, "standing", -90.0, "GAZEBO_ACTOR_STANDING_CC_BY_4_0"),
        (17.5, 11.0, "sitting", -90.0, "GAZEBO_ACTOR_SITTING_CC_BY_4_0"),
        (-17.5, 1.0, "standing", 90.0, "GAZEBO_ACTOR_STANDING_CC_BY_4_0"),
    )
    survivors: list[SurvivorPlacement] = []
    for index, (east, north, state, yaw, variant) in enumerate(
            placements[:survivor_count], start=1):
        point = georef.local.to_wgs84(EnuPoint(east_m=east, north_m=north, up_m=0.0))
        survivors.append(SurvivorPlacement(
            survivor_id=f"hidden_{index:02d}", latitude=point.latitude, longitude=point.longitude,
            body_state=state, yaw_deg=yaw, visual_variant=variant,
        ))
    mission = MissionWorldProfile(
        mission_id=f"phase2_world_alt{altitude_m:g}",
        boundary_wgs84=[(point.latitude, point.longitude) for point in boundary.vertices],
        origin_wgs84=(origin.latitude, origin.longitude, origin.altitude_m or 0.0),
        ground_elevation_m=ScalarValue(value=0, unit="m", provenance="SIMULATION_ONLY"),
        world_dimensions_m=Vector3Value(value=(160, 140, 100), unit="m", provenance="ENGINEERING_ESTIMATE"),
        launch_zone_width_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        launch_zone_length_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        environment_profile_id=environment.profile_id,
        lighting="DAY_CLEAR",
    )
    scenario = SurvivorScenario(scenario_id="phase2_hidden_three", survivors=survivors)
    config = SimulationConfig(
        config_id=f"stage13sim_phase2_{wind_profile.lower()}",
        drone_profile_id=drone.profile_id,
        camera_profile_id=camera.camera_id,
        environment_profile_id=environment.profile_id,
        mission_world_profile_id=mission.mission_id,
        survivor_scenario_id=scenario.scenario_id,
        random_seed=84521,
    )
    realism = (
        get_isolated_sensor_profile(sensor_profile, sensor_component)  # type: ignore[arg-type]
        if sensor_component else get_sensor_realism_profile(sensor_profile)
    )
    return SimulationRunDefinition(
        run_id=run_id, config=config, drone=drone, camera=camera,
        environment=environment, mission=mission, survivors=scenario,
        sensor_profile=realism,
        sensor_seeds=SensorSeeds(
            sensor_seed=sensor_seed,
            wind_seed=environment.wind.random_seed,
            scenario_seed=config.random_seed,
        ),
        phase4_payload_slots=payload_slots,
    )


def build_mission(definition: SimulationRunDefinition, altitude_m: float):
    # Read mission geometry and drone speed only; never inspect hidden survivors.
    boundary = BoundaryService(10).import_boundary(
        "phase2.json",
        json.dumps({
            "type": "Polygon",
            "coordinates": [[[lon, lat] for lat, lon in definition.mission.boundary_wgs84]
                            + [[definition.mission.boundary_wgs84[0][1], definition.mission.boundary_wgs84[0][0]]]],
        }).encode(),
    )
    plan = CoveragePlanner().plan(
        "stage13sim_phase2_pixel_only",
        boundary,
        CoverageRequest(
            search_altitude_m=altitude_m,
            lane_spacing_m=35,
            effective_swath_m=35,
            edge_margin_m=5,
            search_speed_m_s=definition.drone.cruise_speed_mps.value,
            min_planned_coverage_percent=80,
            max_waypoints=100,
        ),
    )
    return ArduCopterMissionBuilder().build(plan, include_rtl=False), plan


def run_live(
    manager: SimulationManager, run_dir: Path, *, altitude_m: float,
    max_sim_time_s: float, wall_timeout_s: float = 900.0,
    experiment_stop_at_rtl: bool = False,
    phase4_service: bool = False,
    phase4_payload_slots: int = 1,
) -> dict[str, object]:
    writer = SimulationArtifactWriter(run_dir)
    pipeline: PixelPerceptionPipeline | None = None
    segmentation: GazeboSegmentationSource | None = None
    segmentation_stop = threading.Event()
    segmentation_frames = []
    segmentation_errors: list[str] = []
    segmentation_thread: threading.Thread | None = None
    started_wall = time.monotonic()
    mode_history: list[str] = []
    airborne = False
    route_complete = False
    rtl_commanded = False
    landed_since: float | None = None
    grounded_evidence_observed = False
    forced_disarm_issued = False
    perception_stopped_for_rtl = False
    initial_pose_source_ns: int | None = None
    run_completed = False
    truth_observer: GazeboTruthObserver | None = None
    truth_observer_metrics: dict[str, object] = {}
    payload_observers: list[GazeboTruthObserver] = []
    payload_observer_metrics: dict[str, object] = {}
    phase4_controller: Phase4TargetServiceController | None = None
    phase4_result = None
    phase4_results = []
    phase4_attempted_targets: set[str] = set()
    phase4_attempted_positions: list[tuple[float, float]] = []
    phase4_payload_adapter: GazeboPayloadAdapter | None = None
    reached_items: set[int] = set()
    last_estimate_source_ns = -1
    witness_bridge: subprocess.Popen[str] | None = None
    witness_socket: socket.socket | None = None
    witness_stop = threading.Event()
    witness_errors: list[str] = []
    witness_count = 0
    try:
        manager.start()
        assert manager.adapter and manager.adapter.router and manager.definition
        adapter = manager.adapter
        # Nominal sensor realism plus software rendering can keep EKF3 in its
        # normal initialization state beyond 90 wall seconds at low RTF.  This
        # is still readiness-gated (not a fixed sleep) and fast-fails on process
        # exit; allow enough wall time for the required healthy position flag.
        adapter.wait_ready(180)
        live_sensor_parameters = {
            name: adapter.parameter_value(name)
            for name in (
                "AHRS_EKF_TYPE", "SIM_GPS1_HNSE", "SIM_GPS1_NOISE", "SIM_GPS1_HZ",
                "SIM_GPS1_LAG_MS", "SIM_GYR1_RND", "SIM_ACC1_RND", "SIM_MAG_RND",
                "SIM_MAG_DELAY", "SIM_BARO_RND", "SIM_BARO_DRIFT", "SIM_BARO_DELAY",
            )
        }
        writer.write_json("live_sensor_parameters.json", {
            "classification": "LIVE_ARDUPILOT_SITL_SENSOR_CONFIGURATION",
            "parameters": live_sensor_parameters,
            "ekf_output_modified_directly": False,
        })
        truth_observer = GazeboTruthObserver(
            world=run_dir.name,
            vehicle=manager.definition.drone.profile_id,
            output=run_dir / "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl",
            interval_s=0.05,
        )
        truth_observer.start()
        if phase4_service:
            for slot_number in range(1, phase4_payload_slots + 1):
                observer = GazeboTruthObserver(
                    world=run_dir.name, vehicle=f"payload_slot_{slot_number}",
                    output=run_dir / f"SIMULATION_ONLY_GROUND_TRUTH_payload_slot_{slot_number}.jsonl",
                    interval_s=0.05,
                )
                observer.start()
                payload_observers.append(observer)
        mission, plan = build_mission(manager.definition, altitude_m)
        transfer, verification = adapter.upload_and_verify(mission)
        origin = GeoPoint(
            latitude=manager.definition.mission.origin_wgs84[0],
            longitude=manager.definition.mission.origin_wgs84[1],
            altitude_m=manager.definition.mission.origin_wgs84[2],
        )
        pose_source_witness = SimulationPoseTimestampWitness(
            run_dir.name + "_pose_source_sidecar")
        pose_subscriber = MAVLinkPoseSubscriber(
            adapter.router,
            vehicle_id="drone_01",
            coordinate_frame=LocalCoordinateFrame(origin),
            clock_sync=ClockSyncEstimator(minimum_samples=12, max_rtt_ms=20, affine=True),
            source_time_witness=pose_source_witness,
            require_sim_source=True,
        )
        # Phase-2C: direct sim clock witness (source-domain, no host pairing).
        # Same-update pairs arrive from the SIM_JSON clock witness patch via the
        # WSL clock_witness_bridge relay. host_received_ns is eviction-only.
        direct_witness = DirectSimClockWitness(
            run_dir.name,
            min_samples=30,
            min_span_ns=5_000_000_000,
            # Phase-2C clock qualification measured P95 residual ~0.8 ms at
            # 200 Hz. 10 ms keeps a defensible gate with >12x margin.
            max_error_ns=10_000_000,
            max_samples=4000,
            window_ns=120_000_000_000,
            max_age_ns=120_000_000_000,
        )
        clock_mapper = DirectSimCaptureClockMapper(direct_witness)
        # Keep Phase-2B mapper for diagnostic snapshot only (not used for geolocation).
        legacy_mapper = SimulationClockMapper(run_dir.name, pose_subscriber.clock_sync)
        def _legacy_boot_diagnostic(message) -> None:
            if message.get_type() == "GLOBAL_POSITION_INT":
                legacy_mapper.observe_boot(
                    int(message.time_boot_ms) * 1_000_000, time.monotonic_ns())
        adapter.router.subscribe(_legacy_boot_diagnostic)
        camera_topic = str(manager.readiness["camera_topic"])
        gazebo_source = GazeboCameraSource(
            camera_topic,
            source_id="drone_01_gazebo_camera",
            camera_profile_id=manager.definition.camera.camera_id,
            expected_width=manager.definition.camera.resolution_width,
            expected_height=manager.definition.camera.resolution_height,
        )
        source = SensorRealismCameraSource(
            gazebo_source,
            manager.definition.sensor_profile.camera,
            sensor_seed=manager.definition.sensor_seeds.sensor_seed,
        )
        if manager.launcher.clock_witness_udp_port:
            witness_bridge_script = ROOT / "rescue_core/simulation/gazebo_bridge/clock_witness_bridge.py"
            witness_tcp_port = _free_loopback_port()
            witness_pid = run_dir / "clock_witness_bridge.pid"
            witness_bridge = subprocess.Popen(
                ["wsl.exe", "-d", "Ubuntu", "--", "python3", _wsl_path(witness_bridge_script),
                 "--udp-port", str(manager.launcher.clock_witness_udp_port),
                 "--tcp-port", str(witness_tcp_port),
                 "--pid-file", _wsl_path(witness_pid)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if witness_bridge.poll() is not None:
                    raise RuntimeError(f"clock witness bridge exited: {witness_bridge.stderr.read()}")
                try:
                    witness_socket = socket.create_connection(("127.0.0.1", witness_tcp_port), timeout=0.5)
                    witness_socket.settimeout(None)
                    break
                except OSError:
                    time.sleep(0.2)
            if witness_socket is None:
                raise RuntimeError("clock witness bridge did not accept a connection")
            reader = witness_socket.makefile("rb", buffering=0)

            def collect_witness() -> None:
                nonlocal witness_count
                last_seq = 0
                try:
                    while not witness_stop.is_set():
                        line = reader.readline(4096)
                        if not line:
                            time.sleep(0.02)
                            continue
                        try:
                            payload = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            continue
                        kind = payload.get("kind", "witness")
                        if kind == "pose_src_time":
                            pose_source_witness.observe(SimulationPoseTimestampWitnessRecord(
                                run_id=run_dir.name,
                                msgid=int(payload["msgid"]), chan=int(payload["chan"]),
                                seq=int(payload["seq"]), ap_boot_ms=int(payload["ap_boot_ms"]),
                                sysid=int(payload["sysid"]), compid=int(payload["compid"]),
                                ap_sim_ns=int(payload["ap_sim_us"]) * 1000,
                                host_received_ns=time.monotonic_ns(),
                            ))
                            continue
                        if kind == "gcs_gpi":
                            continue
                        gz_ns = int(payload["gazebo_fdm_timestamp_ns"])
                        ap_ns = int(payload["ardupilot_time_now_us"]) * 1000
                        seq = int(payload.get("seq", 0))
                        if seq and last_seq and seq != last_seq + 1:
                            if len(witness_errors) < 5:
                                witness_errors.append(f"seq gap {last_seq}->{seq}")
                        last_seq = seq
                        direct_witness.observe_source_pair(gz_ns, ap_ns, time.monotonic_ns())
                        witness_count += 1
                except Exception as exc:  # collector failure cannot stop the mission
                    if not witness_stop.is_set():
                        witness_errors.append(f"{type(exc).__name__}: {exc}")

            threading.Thread(target=collect_witness, name="phase2-clock-witness", daemon=True).start()
            time.sleep(0.5)
            pose_source_witness.reset_metrics()
            pose_subscriber.fallback_samples = 0
            pose_subscriber.fallback_after_first_source = 0
            pose_subscriber.fallback_join_attempts = 0
            for msgid in (30, 31, 33):
                adapter.router.connection.mav.command_long_send(
                    adapter.router.connection.target_system,
                    adapter.router.connection.target_component,
                    511, 0, msgid, 40_000, 0, 0, 0, 0, 0)
        vision_config = load_vision_config(ROOT / "config" / "vision.yaml")
        detector = create_detector(vision_config, backend="onnx", model_id="yolo11n_person_onnx")
        writer.write_json("detector_manifest.json", {
            "model": detector.model.model_dump(mode="json"),
            "backend": "onnx",
            "confidence_threshold": vision_config.detector.confidence_threshold,
            "input_resolution": [vision_config.detector.input_width, vision_config.detector.input_height],
            "preprocessing": "MONO8 -> replicated RGB -> letterbox -> float32 / 255",
            "physical_validation": "PHYSICAL_UNVERIFIED",
        })
        pipeline = PixelPerceptionPipeline(
            source=source,
            detector=detector,
            camera_profile=manager.definition.camera,
            pose_history=pose_subscriber.history,
            mission_id=plan.mission_id,
            coordinate_frame=LocalCoordinateFrame(origin),
            run_dir=run_dir,
            detector_threshold=vision_config.detector.confidence_threshold,
            clock_mapper=clock_mapper,
            # The reference profile's reported GPS accuracy is treated as a
            # one-sigma engineering estimate and converted to a 95% normal
            # bound. IDEAL_CONTROL preserves the frozen Phase-2 convention.
            sensor_accuracy_confidence_multiplier=(
                1.0 if str(manager.definition.sensor_profile.profile_id) == "IDEAL_CONTROL" else 1.645
            ),
        )
        if phase4_service:
            boundary_enu = Polygon([
                (point.east_m, point.north_m) for point in plan.safe_polygon_enu
            ])
            phase4_payload_adapter = GazeboPayloadAdapter(adapter, run_dir)
            phase4_controller = Phase4TargetServiceController(
                adapter=adapter, pipeline=pipeline,
                config=load_rescue_config(ROOT / "config" / "phase4_rescue_orchestration.yaml"),
                frame=LocalCoordinateFrame(origin), boundary_enu=boundary_enu,
                mission_id=plan.mission_id, mission_fingerprint=mission.fingerprint,
                search_first_seq=3, rtl_seq=mission.items[-1].seq + 1,
                run_dir=run_dir, payload=phase4_payload_adapter,
                inventory=PayloadInventory(phase4_payload_slots),
            )
        segmentation = GazeboSegmentationSource(
            f"/{manager.definition.drone.profile_id}/{manager.definition.camera.camera_id}/evaluator_segmentation",
            width=manager.definition.camera.resolution_width,
            height=manager.definition.camera.resolution_height,
        )

        def collect_segmentation() -> None:
            assert segmentation is not None
            try:
                segmentation.start()
                while not segmentation_stop.is_set():
                    frame = segmentation.read()
                    segmentation_frames.append(compact_visibility(
                        frame,
                        survivor_labels=frozenset(101 + index for index in range(len(manager.definition.survivors.survivors))),
                    ))
                    if len(segmentation_frames) > 5000:
                        del segmentation_frames[:1000]
            except Exception as exc:  # evaluator failure cannot alter autonomy
                if not segmentation_stop.is_set():
                    segmentation_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                segmentation.stop()

        gazebo_source.start()
        print('Waiting for first Gazebo frame...')
        gazebo_source.read()
        print('Gazebo frame received! Bridge is fully warmed up.')
        
        segmentation.start()
        # print('Waiting for first Segmentation frame...')
        # segmentation.read()
        # print('Segmentation frame received! Bridge is fully warmed up.')
        adapter.prepare_for_arm(); adapter.arm(); adapter.start_auto_mission()
        # Perception is a search-phase service.  Starting it only once the
        # aircraft is airborne prevents launch-pad views from becoming mission
        # candidates while preserving the live camera-only causal path.
        perception_started = False
        final_sequence = mission.items[-1].seq
        confirmation_seen_at: float | None = None
        wall_deadline = time.monotonic() + wall_timeout_s
        while time.monotonic() < wall_deadline:
            if (perception_started and not perception_stopped_for_rtl
                    and (pipeline.worker_error or not pipeline._thread.is_alive())):
                raise RuntimeError(f"Perception worker stopped: {pipeline.worker_error or 'camera stream ended'}")
            heartbeat = adapter.router.latest("HEARTBEAT")
            position = adapter.router.latest("GLOBAL_POSITION_INT")
            reached = adapter.router.latest("MISSION_ITEM_REACHED")
            current_message = adapter.router.latest("MISSION_CURRENT")
            now = time.monotonic()
            latest_pose_source_ns = (
                pose_subscriber.history.samples[-1].autopilot_boot_time_ns
                if pose_subscriber.history.samples else None)
            if latest_pose_source_ns is not None and initial_pose_source_ns is None:
                initial_pose_source_ns = latest_pose_source_ns
            sim_elapsed = (
                (latest_pose_source_ns - initial_pose_source_ns) / 1e9
                if latest_pose_source_ns is not None and initial_pose_source_ns is not None
                else 0.0)
            if latest_pose_source_ns is not None and latest_pose_source_ns != last_estimate_source_ns:
                sample = pose_subscriber.history.samples[-1]
                gps = adapter.router.latest("GPS_RAW_INT")
                raw_imu = adapter.router.latest("RAW_IMU")
                pressure = adapter.router.latest("SCALED_PRESSURE")
                writer.append_jsonl("autopilot_estimate.jsonl", {
                    "classification": "AUTOPILOT_ESTIMATE_AND_SENSOR_MEASUREMENT",
                    "ap_sim_source_time_ns": sample.autopilot_boot_time_ns,
                    "host_monotonic_time_ns": sample.host_monotonic_time_ns,
                    "host_wall_time_ns": sample.host_wall_time_ns,
                    "simulation_elapsed_s": sim_elapsed,
                    "latitude": sample.latitude,
                    "longitude": sample.longitude,
                    "relative_altitude_m": sample.relative_altitude_m,
                    "velocity_ned_mps": sample.velocity_ned_m_s,
                    "quaternion_body_to_ned": sample.quaternion_body_to_ned,
                    "gps_measurement": ({
                        "latitude": int(gps.lat) / 1e7,
                        "longitude": int(gps.lon) / 1e7,
                        "altitude_msl_m": int(gps.alt) / 1000,
                        "fix_type": int(gps.fix_type),
                        "satellites": int(gps.satellites_visible),
                        "eph_m": (int(gps.eph) / 100 if int(gps.eph) != 65535 else None),
                        "epv_m": (int(gps.epv) / 100 if int(gps.epv) != 65535 else None),
                    } if gps is not None else None),
                    "imu_measurement": ({
                        "xacc_mg": int(raw_imu.xacc), "yacc_mg": int(raw_imu.yacc),
                        "zacc_mg": int(raw_imu.zacc), "xgyro_millirad_s": int(raw_imu.xgyro),
                        "ygyro_millirad_s": int(raw_imu.ygyro), "zgyro_millirad_s": int(raw_imu.zgyro),
                        "xmag_mgauss": int(raw_imu.xmag), "ymag_mgauss": int(raw_imu.ymag),
                        "zmag_mgauss": int(raw_imu.zmag),
                    } if raw_imu is not None else None),
                    "barometer_measurement": ({
                        "absolute_pressure_hpa": float(pressure.press_abs),
                        "differential_pressure_hpa": float(pressure.press_diff),
                        "temperature_cdeg": int(pressure.temperature),
                    } if pressure is not None else None),
                    "timestamp_source": sample.timestamp_source,
                })
                last_estimate_source_ns = latest_pose_source_ns
            relative_altitude = int(position.relative_alt) / 1000 if position is not None else 0.0
            horizontal_speed = (
                math.hypot(int(position.vx) / 100, int(position.vy) / 100)
                if position is not None else 0.0
            )
            if relative_altitude > 1.0:
                airborne = True
            if (
                airborne
                and relative_altitude >= max(2.0, altitude_m * 0.75)
                and horizontal_speed >= 0.5
                and not perception_started
            ):
                segmentation_thread = threading.Thread(
                    target=collect_segmentation, name="phase2-evaluator-segmentation", daemon=True,
                )
                segmentation_thread.start()
                pipeline.start()
                perception_started = True
            if heartbeat:
                mode = mavutil.mode_string_v10(heartbeat)
                if not mode_history or mode_history[-1] != mode:
                    mode_history.append(mode)
                    writer.append_jsonl("events.jsonl", {"type": "MODE_CHANGED", "mode": mode, "simulation_elapsed_s": sim_elapsed})
                armed = bool(int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                if experiment_stop_at_rtl and rtl_commanded and mode == "RTL":
                    break
                if airborne and rtl_commanded and not armed:
                    break
            if pipeline.confirmed() and confirmation_seen_at is None:
                confirmation_seen_at = sim_elapsed
            if reached is not None:
                reached_items.add(int(reached.seq))
            if (
                phase4_service and phase4_controller is not None
                and pipeline.confirmed() and current_message is not None
                and 3 <= int(current_message.seq) <= final_sequence
                and len(phase4_results) < phase4_payload_slots
                and phase4_controller.inventory.remaining > 0
                and (selected := next((item for item in pipeline.confirmed()
                                      if item.target_id not in phase4_attempted_targets
                                      and all(math.hypot(item.east_m-east, item.north_m-north) > 5.0
                                              for east, north in phase4_attempted_positions)), None)) is not None
            ):
                phase4_attempted_targets.add(selected.target_id)
                phase4_attempted_positions.append((selected.east_m, selected.north_m))
                coverage_before = max(0.0, min(100.0,
                    100.0 * (int(current_message.seq) - 3) / max(1, final_sequence - 3)))
                phase4_result = phase4_controller.service(
                    selected, current_seq=int(current_message.seq),
                    reached_items=sorted(reached_items), coverage_percent=coverage_before,
                )
                phase4_results.append(phase4_result)
                writer.write_json("phase4_service_result.json", asdict(phase4_result))
                writer.write_json("phase4_service_results.json", [asdict(item) for item in phase4_results])
                if phase4_payload_adapter:
                    writer.write_json("payload_release_commands.json", phase4_payload_adapter.release_records)
            if reached is not None and int(reached.seq) == final_sequence:
                route_complete = True
            # Finish the independent coverage route, not a truth/detection-directed search.
            if (route_complete or sim_elapsed >= max_sim_time_s) and not rtl_commanded:
                adapter.rtl(); rtl_commanded = True
                writer.append_jsonl("events.jsonl", {"type": "RTL_COMMANDED", "simulation_elapsed_s": sim_elapsed})
                # Search pixels can no longer affect mission output after RTL.
                # Stop inference promptly so landing/disarm qualification is
                # not needlessly CPU-starved by LLVMPipe + ONNX.
                if perception_started:
                    pipeline.stop()
                    perception_stopped_for_rtl = True
                    segmentation_stop.set()
            if airborne and rtl_commanded and position and heartbeat and armed:
                grounded = (
                    abs(int(position.relative_alt) / 1000) < 0.15
                    and abs(int(position.vz) / 100) < 0.12
                    and horizontal_speed < 0.25
                )
                grounded_evidence_observed = grounded_evidence_observed or grounded
                landed_since = (landed_since or now) if grounded else None
                if landed_since is not None and now - landed_since >= 8:
                    adapter.force_disarm_grounded_simulation(grounded_evidence=True)
                    forced_disarm_issued = True
            time.sleep(0.03)
        if not rtl_commanded:
            adapter.rtl(); rtl_commanded = True
        heartbeat = adapter.router.latest("HEARTBEAT")
        disarmed = bool(heartbeat) and not bool(int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if perception_started and not perception_stopped_for_rtl:
            pipeline.stop()
        pipeline.persist()
        segmentation_stop.set()
        segmentation.stop()
        if segmentation_thread:
            segmentation_thread.join(20)
        if truth_observer:
            truth_observer_metrics = truth_observer.stop()
            truth_observer = None
        for slot_number, observer in enumerate(payload_observers, start=1):
            payload_observer_metrics[f"slot_{slot_number}"] = observer.stop()
        payload_observers.clear()

        evaluation = evaluate_after_inference(run_dir, pipeline, segmentation_frames)
        source_metrics = gazebo_source.metrics.as_dict()
        camera_realism_metrics = asdict(source.metrics)
        inference = pipeline.metrics.inference_ms
        result = {
            "classification": "PIXEL_ONLY_VIRTUAL_PERCEPTION",
            "proof_source": "LIVE_GAZEBO_CAMERA_PIXELS",
            "mock_detector": False,
            "synthetic_bbox": False,
            "distance_detection": False,
            "truth_pose_used_for_geolocation": False,
            "camera_topic": camera_topic,
            "segmentation_topic": f"/{manager.definition.drone.profile_id}/{manager.definition.camera.camera_id}/evaluator_segmentation",
            "mission_uploaded": transfer.success,
            "mission_verified": verification.valid,
            "mission_plan_fingerprint": mission.fingerprint,
            "mode_history": mode_history,
            "airborne": airborne,
            "moving_perception": any(
                observation.interpolated_pose.sample.velocity_ned_m_s
                and math.hypot(*observation.interpolated_pose.sample.velocity_ned_m_s[:2]) > 1.0
                for observation in pipeline.observations
            ),
            "rtl": "RTL" in mode_history,
            "disarmed": disarmed,
            "grounded_kinematics_observed": grounded_evidence_observed,
            "forced_disarm_issued": forced_disarm_issued,
            "termination_wall_timeout_s": wall_timeout_s,
            "transport": source_metrics,
            "camera_sensor_realism": camera_realism_metrics,
            "sensor_profile_id": manager.definition.sensor_profile.profile_id,
            "sensor_seeds": manager.definition.sensor_seeds.model_dump(mode="json"),
            "truth_observer": truth_observer_metrics,
            "payload_truth_observer": payload_observer_metrics,
            "phase4_service": asdict(phase4_result) if phase4_result else None,
            "phase4_services": [asdict(item) for item in phase4_results],
            "phase4_payload_inventory": (
                [slot.model_dump(mode="json") for slot in phase4_controller.inventory.slots]
                if phase4_controller else []
            ),
            "vision_queue_drops": pipeline.agent.queue.dropped_total,
            "maximum_queue_depth": pipeline.agent.queue.max_observed_depth,
            "frames_inferred": pipeline.metrics.results,
            "detector_fps_wall": pipeline.agent.status()["detector_fps"],
            "inference_p50_ms": statistics.median(inference) if inference else None,
            "inference_p95_ms": _percentile(inference, 95),
            "segmentation_frames": len(segmentation_frames),
            "segmentation_errors": segmentation_errors,
            "raw_person_detections": pipeline.metrics.raw_person_detections,
            "geolocated_observations": pipeline.metrics.geolocated_observations,
            "confirmed_targets": len({item.target_id for item in pipeline.confirmed()}),
            "renderer": json.loads((run_dir / "environment_manifest.json").read_text(encoding="utf-8"))["render_engine"],
            "wall_duration_s": time.monotonic() - started_wall,
            "hardware_gate_h1": "PENDING",
            "clock_mapping": direct_witness.snapshot(),
            **evaluation,
        }
        result["pixel_pipeline_smoke_passed"] = all((
            result["raw_person_detections"] > 0,
            result["geolocated_observations"] > 0,
            result["confirmed_targets"] > 0,
            result["moving_perception"],
            result["mission_verified"],
            result["airborne"],
            result["rtl"],
            result["disarmed"],
            result["segmentation_frames"] > 0,
            result["measured_target_errors"] > 0,
        ))
        direct_clock_snap = direct_witness.snapshot()
        direct_clock_quality = direct_clock_snap.get("quality", "UNINITIALIZED")
        direct_clock_valid = direct_clock_quality == "VALID"
        result["direct_sim_clock_quality"] = direct_clock_quality
        result["direct_sim_clock_sample_count"] = direct_clock_snap.get("sample_count", 0)
        result["witness_samples_collected"] = witness_count
        result["witness_sequence_errors"] = witness_errors
        eligible_pose_rows = [row for row in pipeline.results if row.get("detections")]
        result["capture_pose_alignment_verified"] = bool(eligible_pose_rows) and all(
            row.get("capture_pose_alignment_verified") is True for row in eligible_pose_rows
        )
        pose_source_snap = pose_source_witness.snapshot()
        result["pose_timestamp_source"] = "SITL_SIM_SOURCE_SIDECAR"
        result["pose_values_source"] = "MAVLINK_EKF"
        result["host_time_used_for_pose_selection"] = False
        result["gazebo_truth_pose_used"] = False
        result["pose_source_join"] = pose_source_snap["join_metrics"]
        proof = {
            "run_id": run_dir.name,
            "proof_source": "LIVE_GAZEBO_CAMERA_PIXELS",
            "pose_timestamp_source": "SITL_SIM_SOURCE_SIDECAR",
            "pose_values_source": "MAVLINK_EKF",
            "host_time_used_for_pose_selection": False,
            "gazebo_truth_pose_used": False,
            "direct_clock_quality": direct_clock_quality,
            "source_time_join": pose_source_snap["join_metrics"],
            "rendered": result["frames_inferred"] > 0,
            "yolo11n": result["raw_person_detections"] > 0,
            "confirmed": result["confirmed_targets"] > 0,
            "geolocated": result["geolocated_observations"] > 0,
            "best_geolocation_error_m": result.get("best_geolocation_error_m"),
            "truth_inside_uncertainty_count": result.get("truth_inside_uncertainty_count"),
        }
        writer.write_json("PIXEL_TO_CONFIRMED_GEOLOCATION_PROOF.json", proof)
        blockers = []
        if not direct_clock_valid:
            blockers.append("DIRECT_SIM_CLOCK_NOT_VALID")
        if witness_errors:
            blockers.append(f"CLOCK_WITNESS_STREAM_ERRORS: {witness_errors[:3]}")
        if not result["pixel_pipeline_smoke_passed"]:
            blockers.append("GOLD_PIXEL_PIPELINE_OR_TERMINATION_FAILED")
        result["qualification_blockers"] = blockers
        result["gold_requalified"] = not blockers
        # This runner qualifies only the gold chain. The aggregate Phase-2E
        # qualification remains fail-closed until every separate gate passes.
        result["passed"] = False
        result["phase2_status"] = (
            "GOLD_REQUALIFIED_PHASE2E_PENDING" if result["gold_requalified"] else "BLOCKED"
        )
        sim_span_s = ((gazebo_source.metrics.last_sim_time_ns or 0) - (gazebo_source.metrics.first_sim_time_ns or 0)) / 1e9
        wall_span_s = ((gazebo_source.metrics.last_wall_receive_ns or 0) - (gazebo_source.metrics.first_wall_receive_ns or 0)) / 1e9
        result["transport_sim_duration_s"] = sim_span_s
        result["camera_interval_real_time_factor"] = sim_span_s / wall_span_s if wall_span_s else None
        result["transport_60_sim_seconds"] = sim_span_s >= 60
        result["motion_blur_model"] = manager.definition.sensor_profile.camera.motion_blur_model
        if phase4_service:
            released_services = sum(bool(item.released and item.resumed) for item in phase4_results)
            required_services = min(phase4_payload_slots, len(manager.definition.survivors.survivors))
            result["phase4_passed"] = bool(
                released_services >= required_services
                and all(item.assigned and item.approach_reached and item.reacquired
                        and item.aligned and item.gate_passed and item.released and item.resumed
                        for item in phase4_results[:required_services])
                and result["rtl"] and result["disarmed"]
                and direct_clock_valid
            )
            result["passed"] = result["phase4_passed"]
            result["phase4_status"] = "PASS" if result["passed"] else "FAIL"
        writer.write_json("frame_transport_metrics.json", {
            **source_metrics, "vision_drops": result["vision_queue_drops"],
            "detector_frames": result["frames_inferred"],
            "max_queue_depth": result["maximum_queue_depth"],
        })
        writer.write_json("DIRECT_SIM_CLOCK_EVIDENCE.json", {
            **direct_clock_snap,
            "backend": "SIM_JSON",
            "ardupilot_commit": "af2a1bafc8ab993418e6714cbc2674dcc178d11a",
            "ardupilot_gazebo_commit": "082a0fe231f6e63bc8d1598f1cba461d9e2ea7f5",
            "no_time_sync": 0,
            "lock_step": 1,
            "clock_chain": "GAZEBO_SIM -> ARDUPILOT_SIM_SOURCE (direct, no host transport)",
            "source_domain_capture": "SIM_JSON_CLOCK_WITNESS_PATCH",
            "rtf": result.get("camera_interval_real_time_factor"),
            "witness_samples_collected": witness_count,
            "witness_sequence_errors": witness_errors,
            "legacy_clock_mapping": legacy_mapper.snapshot(time.monotonic_ns()),
        })
        if pipeline.clock_mapper:
            writer.write_json("CLOCK_MAPPING_EVIDENCE.json", direct_witness.snapshot())
        writer.write_json("perception_metrics.json", evaluation)
        writer.write_json("PERCEPTION_PIPELINE_BENCHMARK.json", {
            "camera_source_fps_sim_time": source_metrics["simulation_time_source_fps"],
            "camera_source_fps_wall_time": source_metrics["wall_time_receive_fps"],
            "detector_fps_wall": result["detector_fps_wall"],
            "inference_p50_ms": result["inference_p50_ms"],
            "inference_p95_ms": result["inference_p95_ms"],
            "maximum_frame_age_ms": source_metrics["max_frame_age_ms"],
            "maximum_queue_depth": result["maximum_queue_depth"],
            "bridge_drops": source_metrics["frames_dropped_bridge"],
            "vision_drops": result["vision_queue_drops"],
            "renderer": result["renderer"],
            "configured_sensor_fps": manager.definition.camera.fps.value,
        })
        writer.write_json("phase2_run_summary.json", result)
        writer.write_json("camera_latency_metrics.json", camera_realism_metrics)
        writer.write_json("camera_image_realism.json", {
            "pixel_format": "MONO8",
            "noise_stddev_dn": manager.definition.sensor_profile.camera.gaussian_noise_stddev_dn,
            "brightness_gain": manager.definition.sensor_profile.camera.brightness_gain,
            "contrast_gain": manager.definition.sensor_profile.camera.contrast_gain,
            "exposure_preset": manager.definition.sensor_profile.camera.exposure_preset,
            "motion_blur_model": manager.definition.sensor_profile.camera.motion_blur_model,
            "rolling_shutter_model": manager.definition.sensor_profile.camera.rolling_shutter_model,
            "metrics": camera_realism_metrics,
        })
        run_completed = True
        return result
    except Exception as exc:
        writer.write_json("phase2_run_failure.json", {
            "passed": False, "phase2_status": "BLOCKED",
            "error": f"{type(exc).__name__}: {exc}",
            "hardware_gate_h1": "PENDING", "physical_hardware_touched": False,
        })
        raise
    finally:
        witness_stop.set()
        if witness_socket:
            try: witness_socket.close()
            except Exception: pass
        if witness_bridge:
            try:
                pid_file = run_dir / "clock_witness_bridge.pid"
                if pid_file.exists():
                    subprocess.run(
                        ["wsl.exe", "-d", "Ubuntu", "--", "kill", pid_file.read_text().strip()],
                        capture_output=True, timeout=5,
                    )
            except OSError:
                pass
            try: witness_bridge.terminate()
            except Exception: pass
            try: witness_bridge.wait(timeout=5)
            except Exception: pass
        if pipeline and pipeline._thread.is_alive():
            try: pipeline.stop()
            except Exception: pass
        if pipeline and not pipeline._thread.is_alive():
            pipeline.persist()
        segmentation_stop.set()
        if segmentation:
            segmentation.stop()
        if segmentation_thread and segmentation_thread.is_alive():
            segmentation_thread.join(5)
        if truth_observer:
            try: truth_observer.stop()
            except Exception: pass
        for observer in payload_observers:
            try: observer.stop()
            except Exception: pass
        manager.stop(completed=run_completed)


def evaluate_after_inference(run_dir: Path, pipeline: PixelPerceptionPipeline, segmentation_frames: list) -> dict[str, object]:
    # This is the first point at which hidden truth is read.
    truth = json.loads((run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").read_text(encoding="utf-8"))
    evaluator = PerceptionEvaluator(iou_threshold=0.30)
    tp = fp = fn = 0
    ious: list[float] = []
    visible_frames = 0
    per_frame_rows = []
    matched_detection_labels: dict[tuple[str, int], int] = {}
    unpaired_frames = 0
    for envelope in pipeline.results:
        result = detection_result_from_dict(envelope)
        sim_ns = int(envelope.get("sim_capture_time_ns") or 0)
        if not segmentation_frames:
            unpaired_frames += 1
            continue
        truth_frame = min(segmentation_frames, key=lambda item: abs(item.sim_capture_time_ns - sim_ns))
        if abs(truth_frame.sim_capture_time_ns - sim_ns) > 100_000_000:
            unpaired_frames += 1
            continue
        boxes = list(truth_frame.boxes)
        visible_frames += bool(boxes)
        compared = evaluator.evaluate(result.detections, boxes)
        tp += compared.true_positives; fp += len(compared.false_positive_indices); fn += len(compared.false_negative_indices)
        ious.extend(match.iou for match in compared.matches)
        for match in compared.matches:
            matched_detection_labels[(result.frame_id, match.detection_index)] = boxes[match.truth_index].evaluator_label
        per_frame_rows.append({
            "frame_id": result.frame_id,
            "sim_capture_time_ns": sim_ns,
            "truth_frame_id": truth_frame.frame_id,
            "pairing_delta_ns": truth_frame.sim_capture_time_ns - sim_ns,
            "truth_boxes": [asdict(box) for box in boxes],
            "matches": [asdict(match) for match in compared.matches],
            "false_positive_indices": compared.false_positive_indices,
            "false_negative_indices": compared.false_negative_indices,
        })
    target_errors = []
    truth_survivors = truth["survivors"]
    label_to_survivor = {101 + index: survivor for index, survivor in enumerate(truth_survivors)}
    confirmed_false_positive_targets: list[str] = []
    unassociated_targets: list[str] = []
    for estimate in pipeline.confirmed():
        links = [pipeline.observation_detections.get(item) for item in estimate.inlier_observation_ids]
        if not links or any(link is None for link in links):
            unassociated_targets.append(estimate.target_id)
            continue
        label_votes = Counter(
            matched_detection_labels[link]
            for link in links
            if link in matched_detection_labels and matched_detection_labels[link] in label_to_survivor
        )
        if not label_votes:
            confirmed_false_positive_targets.append(estimate.target_id)
            continue
        nearest = label_to_survivor[label_votes.most_common(1)[0][0]]
        error = math.hypot(estimate.east_m - nearest["position_enu_m"][0], estimate.north_m - nearest["position_enu_m"][1])
        target_errors.append({
            "classification": "EVALUATOR_ONLY_PREDICTION_VS_TRUTH",
            "target_id": estimate.target_id,
            "survivor_id": nearest["survivor_id"],
            "estimated_enu_m": [estimate.east_m, estimate.north_m],
            "SIMULATION_ONLY_GROUND_TRUTH_enu_m": nearest["position_enu_m"][:2],
            "horizontal_error_m": error,
            "reported_uncertainty_m": estimate.uncertainty_m,
            "estimated_wgs84": [estimate.latitude, estimate.longitude],
            "SIMULATION_ONLY_GROUND_TRUTH_wgs84": nearest["position_wgs84"],
            "truth_inside_uncertainty": error <= estimate.uncertainty_m,
            "proof_source": "CAMERA_PIXELS",
            "truth_association_source": "MATCHED_SEGMENTATION_PIXELS_AFTER_INFERENCE",
        })
    (run_dir / "perception_evaluation.json").write_text(json.dumps({
        "classification": "SIMULATION_EVALUATOR_ONLY",
        "frames": per_frame_rows,
        "target_errors": target_errors,
    }, indent=2, default=str), encoding="utf-8")
    (run_dir / "geolocation_metrics.json").write_text(json.dumps({
        "classification": "EVALUATOR_ONLY_UNQUALIFIED_TIMING",
        "unique_final_target_errors": target_errors,
        "unassociated_targets": unassociated_targets,
    }, indent=2), encoding="utf-8")
    outcomes = []
    for index, survivor in enumerate(truth_survivors):
        nearest_errors = [row for row in target_errors if row["survivor_id"] == survivor["survivor_id"]]
        label = 101 + index
        visible_times = [frame.sim_capture_time_ns for frame in segmentation_frames if any(box.evaluator_label == label for box in frame.boxes)]
        hit_times = [int(row["sim_capture_time_ns"]) for row in pipeline.results if any(
            matched_detection_labels.get((str(row["frame_id"]), detection_index)) == label
            for detection_index in range(len(row["detections"]))
        )]
        outcome = (
            "CONFIRMED_AND_GEOLOCATED" if nearest_errors
            else "DETECTED_BUT_NOT_CONFIRMED" if hit_times
            else "VISIBLE_BUT_NOT_DETECTED" if visible_times
            else "NEVER_VISIBLE"
        )
        row = {
            "survivor_id": survivor["survivor_id"], "outcome": outcome,
            "first_visible_sim_ns": min(visible_times) if visible_times else None,
            "first_detector_hit_sim_ns": min(hit_times) if hit_times else None,
            "visible_frames": len(visible_times), "detector_hit_frames": len(hit_times),
            "visible_to_detection_latency_s": (min(hit_times) - min(visible_times)) / 1e9 if hit_times and visible_times else None,
            "visibility_scope": "collected search-phase camera frames only",
        }
        outcomes.append(row)
        (run_dir / f"survivor_evaluation_{survivor['survivor_id']}.json").write_text(json.dumps({
            "classification": "SIMULATION_EVALUATOR_ONLY",
            "survivor_id": survivor["survivor_id"],
            "SIMULATION_ONLY_GROUND_TRUTH_position": survivor["position_enu_m"],
            "pose": survivor["body_state"],
            "confirmed_estimates": nearest_errors,
            **row,
        }, indent=2), encoding="utf-8")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    evidence = target_errors[0] if target_errors else {
        "proof_source": "CAMERA_PIXELS", "outcome": "NO_CONFIRMED_TARGET",
    }
    (run_dir / "PIXEL_ONLY_PERCEPTION_EVIDENCE.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return {
        "truth_visible_person_frames": visible_frames,
        "detector_true_positives": tp,
        "detector_false_positives": fp,
        "detector_false_negatives": fn,
        "detection_precision": precision,
        "detection_recall": recall,
        "bbox_iou_p50": statistics.median(ious) if ious else None,
        "bbox_iou_p95": _percentile(ious, 95),
        "measured_target_errors": len(target_errors),
        "best_geolocation_error_m": min((row["horizontal_error_m"] for row in target_errors), default=None),
        "worst_geolocation_error_m": max((row["horizontal_error_m"] for row in target_errors), default=None),
        "truth_inside_uncertainty_count": sum(bool(row["truth_inside_uncertainty"]) for row in target_errors),
        "survivors_confirmed_and_geolocated": len({row["survivor_id"] for row in target_errors}),
        "confirmed_false_positive_targets": confirmed_false_positive_targets,
        "unassociated_targets": unassociated_targets,
        "evaluation_paired_frames": len(per_frame_rows),
        "evaluation_unpaired_frames": unpaired_frames,
        "recall_denominator": "truth boxes in inferred frames paired within 100 ms; not full mission recall",
        "survivor_outcomes": outcomes,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=f"stage13sim_phase2_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--speed-mps", type=float, default=3.0)
    parser.add_argument("--altitude-m", type=float, default=8.0)
    parser.add_argument("--wind", choices=("PERCEPTION_CALM", "PERCEPTION_MODERATE"), default="PERCEPTION_MODERATE")
    parser.add_argument("--max-sim-time-s", type=float, default=180.0)
    parser.add_argument("--wall-timeout-s", type=float, default=900.0)
    parser.add_argument("--experiment-stop-at-rtl", action="store_true")
    parser.add_argument("--single-survivor", action="store_true")
    parser.add_argument("--camera-profile", type=Path, default=None)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument(
        "--sensor-profile",
        choices=("IDEAL_CONTROL", "REFERENCE_NOMINAL", "REFERENCE_CHALLENGING"),
        default="IDEAL_CONTROL",
    )
    parser.add_argument("--sensor-seed", type=int, default=31003)
    parser.add_argument("--sensor-component", choices=("GPS", "IMU", "COMPASS", "BAROMETER", "CAMERA"), default=None)
    parser.add_argument("--phase4-service", action="store_true")
    parser.add_argument("--phase4-payload-slots", type=int, choices=range(1, 4), default=1)
    parser.add_argument(
        "--output-dir",
        type=Path, default=None,
        help=(
            "Run artifact directory (default: %(default)s = data/simulation_runs/). "
            "Use a path on WSL /tmp or another volume if C: drive is near-full."
        ),
    )
    args = parser.parse_args()
    output_dir = args.output_dir or (ROOT / "data" / "simulation_runs")
    witness_udp_port = _free_loopback_port()
    manager = SimulationManager(output_dir, launcher=WslGazeboLauncher(clock_witness_udp_port=witness_udp_port))

    definition = build_definition(
        args.run_id, speed_mps=args.speed_mps, altitude_m=args.altitude_m,
        wind_profile=args.wind, survivor_count=1 if args.single_survivor else 3,
        camera_profile_path=args.camera_profile, sensor_profile=args.sensor_profile,
        sensor_seed=args.sensor_seed, sensor_component=args.sensor_component,
        payload_slots=args.phase4_payload_slots if args.phase4_service else 0)
    run_dir = manager.prepare(definition)
    if args.generate_only:
        print(json.dumps({"generated": True, "run_dir": str(run_dir)}, indent=2)); return 0
    result = run_live(
        manager, run_dir, altitude_m=args.altitude_m,
        max_sim_time_s=args.max_sim_time_s, wall_timeout_s=args.wall_timeout_s,
        experiment_stop_at_rtl=args.experiment_stop_at_rtl,
        phase4_service=args.phase4_service,
        phase4_payload_slots=args.phase4_payload_slots,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
