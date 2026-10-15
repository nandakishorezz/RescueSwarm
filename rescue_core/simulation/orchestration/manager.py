"""Stage 13SIM generation/process orchestration; never an autopilot controller."""

from __future__ import annotations

import time
import json
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from ..artifacts import SimulationArtifactWriter
from ..gazebo import OwnedSimulationProcesses, WslGazeboLauncher, inspect_simulator_environment
from ..georef import SimulationGeoreference
from ..profiles import CameraProfile, DroneProfile, EnvironmentProfile, MissionWorldProfile, SimulationConfig, SurvivorScenario
from ..sensor_realism import SensorRealismProfile, SensorSeeds, get_sensor_realism_profile
from ..truth import SimulationGroundTruth, WorldTruthSurvivor
from ..wind import generate_wind_schedule
from ..world import GazeboVehicleModelBuilder, WorldBuilder
from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter


class SimulationState(StrEnum):
    IDLE = "IDLE"
    CONFIGURING = "CONFIGURING"
    VALIDATING = "VALIDATING"
    GENERATING = "GENERATING"
    STARTING_GAZEBO = "STARTING_GAZEBO"
    STARTING_SITL = "STARTING_SITL"
    WAITING_FOR_READY = "WAITING_FOR_READY"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SimulationClock(BaseModel):
    started_wall_time: datetime | None = None
    sim_time_s: float = 0.0
    real_time_factor: float = 1.0

    def start(self) -> None:
        self.started_wall_time = datetime.now(UTC)


class SimulationRunDefinition(BaseModel):
    run_id: str
    config: SimulationConfig
    drone: DroneProfile
    camera: CameraProfile
    environment: EnvironmentProfile
    mission: MissionWorldProfile
    survivors: SurvivorScenario
    sensor_profile: SensorRealismProfile = get_sensor_realism_profile("IDEAL_CONTROL")
    sensor_seeds: SensorSeeds = SensorSeeds()
    phase4_payload_slots: int = 0
    fleet_count: int = 1
    fleet_spawn_offset_m: float = 2.0
    disable_servicer_camera: bool = False


class SimulationManager:
    def __init__(self, runs_root: Path, launcher: WslGazeboLauncher | None = None) -> None:
        self.runs_root = runs_root
        self.launcher = launcher or WslGazeboLauncher()
        self.state = SimulationState.IDLE
        self.definition: SimulationRunDefinition | None = None
        self.run_dir: Path | None = None
        self.processes: OwnedSimulationProcesses | None = None
        self.clock = SimulationClock()
        self.error: str | None = None
        self.adapter: SITLAutopilotAdapter | None = None
        self.readiness: dict[str, object] = {}

    def prepare(self, definition: SimulationRunDefinition) -> Path:
        if self.state not in {SimulationState.IDLE, SimulationState.COMPLETED, SimulationState.FAILED, SimulationState.READY}:
            raise RuntimeError(f"cannot prepare while simulation is {self.state}")
        self.state = SimulationState.VALIDATING
        if definition.config.drone_profile_id != definition.drone.profile_id:
            raise ValueError("SimulationConfig drone profile reference does not match supplied profile")
        if definition.config.camera_profile_id != definition.camera.camera_id:
            raise ValueError("SimulationConfig camera profile reference does not match supplied profile")
        if definition.config.environment_profile_id != definition.environment.profile_id:
            raise ValueError("SimulationConfig environment profile reference does not match supplied profile")
        if definition.config.mission_world_profile_id != definition.mission.mission_id:
            raise ValueError("SimulationConfig mission world reference does not match supplied profile")
        if definition.config.survivor_scenario_id != definition.survivors.scenario_id:
            raise ValueError("SimulationConfig survivor scenario reference does not match supplied scenario")
        self.state = SimulationState.GENERATING
        run_dir = self.runs_root / definition.run_id
        if run_dir.exists():
            raise FileExistsError(f"simulation run already exists: {definition.run_id}")
        writer = SimulationArtifactWriter(run_dir)
        # Keep the exact non-runtime launch definition so a backend restart can
        # safely reattach to this generated world.  Process ownership itself is
        # never restored; only an explicit Mission start may launch it again.
        writer.write_json("simulation_run_definition.json", definition.model_dump(mode="json"))
        # Preserve the Phase-1/2 world fingerprint contract. Sensor/seed files
        # are separately versioned Phase-3 evidence and do not rewrite it.
        fingerprint = definition.config.fingerprint(
            definition.drone, definition.camera, definition.environment,
            definition.mission, definition.survivors,
        )
        origin = GeoPoint(latitude=definition.mission.origin_wgs84[0], longitude=definition.mission.origin_wgs84[1], altitude_m=definition.mission.origin_wgs84[2])
        georef = SimulationGeoreference(origin)
        truth_survivors = []
        for survivor in definition.survivors.survivors:
            wgs = GeoPoint(latitude=survivor.latitude, longitude=survivor.longitude, altitude_m=definition.mission.origin_wgs84[2] + survivor.up_m)
            enu = georef.local.to_enu(wgs)
            gazebo = georef.enu_to_gazebo(enu)
            truth_survivors.append(WorldTruthSurvivor(
                simulation_entity=f"survivor_{survivor.survivor_id}", survivor_id=survivor.survivor_id,
                position_gazebo_m=(gazebo.x_m, gazebo.y_m, gazebo.z_m),
                position_enu_m=(enu.east_m, enu.north_m, enu.up_m),
                position_wgs84=(wgs.latitude, wgs.longitude, wgs.altitude_m or 0.0),
                body_state=survivor.body_state, enabled=survivor.enabled,
            ))
        truth = SimulationGroundTruth(run_id=definition.run_id, simulation_fingerprint=fingerprint, survivors=truth_survivors)
        writer.write_json("simulation_config.json", {**definition.config.model_dump(mode="json"), "simulation_fingerprint": fingerprint})
        writer.write_json("drone_profile.json", definition.drone)
        writer.write_json("camera_profile.json", definition.camera)
        writer.write_json("environment_profile.json", definition.environment)
        writer.write_json("sensor_profile.json", {
            **definition.sensor_profile.model_dump(mode="json"),
            "ardupilot_sensor_parameters": definition.sensor_profile.ardupilot_sensor_parameters(),
            "sensor_layer": "ARDUPILOT_SITL_MEASUREMENT_BACKENDS_BEFORE_EKF3",
            "world_truth_to_ekf_bypass": False,
        })
        writer.write_json("sensor_seed.json", definition.sensor_seeds)
        writer.write_text("sensor_realism.parm", definition.sensor_profile.parameter_file())
        writer.write_json("SIMULATION_ONLY_GROUND_TRUTH_survivors.json", truth)
        writer.write_json("georeference.json", {
            "axis_contract": "Mission ENU E,N,U equals Gazebo X,Y,Z",
            "origin_wgs84": definition.mission.origin_wgs84,
            "T_gazebo_from_enu": georef.t_gazebo_from_enu.tolist(),
            "T_enu_from_gazebo": georef.t_enu_from_gazebo.tolist(),
        })
        writer.write_json("environment_manifest.json", inspect_simulator_environment().model_dump(mode="json"))
        for sample in generate_wind_schedule(definition.environment.wind, 120.0, 0.1):
            writer.append_jsonl("SIMULATION_ONLY_GROUND_TRUTH_wind.jsonl", sample)
        truth_redirects = {
            "survivor_ground_truth.json": "SIMULATION_ONLY_GROUND_TRUTH_survivors.json",
            "wind_truth.jsonl": "SIMULATION_ONLY_GROUND_TRUTH_wind.jsonl",
            "vehicle_truth.jsonl": "SIMULATION_ONLY_GROUND_TRUTH_vehicle.jsonl",
        }
        for legacy_name, observer_only_name in truth_redirects.items():
            redirect = {
                "classification": "SIMULATION_ONLY_GROUND_TRUTH_REDIRECT",
                "contains_ground_truth": False,
                "observer_only_artifact": observer_only_name,
                "autonomy_ingestion_permitted": False,
            }
            if legacy_name.endswith(".jsonl"):
                writer.write_text(legacy_name, json.dumps(redirect, separators=(",", ":")) + "\n")
            else:
                writer.write_json(legacy_name, redirect)
        vehicle_sdf = GazeboVehicleModelBuilder().build(
            definition.drone, definition.camera, payload_slots=definition.phase4_payload_slots,
        )
        world_sdf = WorldBuilder().build(
            definition.mission, definition.drone, definition.environment.wind,
            definition.survivors, payload_slots=definition.phase4_payload_slots,
            fleet_count=definition.fleet_count,
            fleet_spawn_offset_m=definition.fleet_spawn_offset_m,
        )
        writer.write_text("generated_vehicle.sdf", vehicle_sdf)
        writer.write_text("generated_world.sdf", world_sdf)
        model_dir = run_dir / "models" / definition.drone.profile_id
        model_dir.mkdir(parents=True)
        (model_dir / "model.sdf").write_text(vehicle_sdf, encoding="utf-8")
        (model_dir / "model.config").write_text(
            f"<?xml version='1.0'?><model><name>{definition.drone.profile_id}</name><version>1.0</version><sdf version='1.9'>model.sdf</sdf><description>Generated Stage 13SIM reference vehicle; not measured hardware.</description></model>\n",
            encoding="utf-8",
        )
        for index in range(1, definition.fleet_count + 1):
            vehicle_id = f"drone_{index:02d}"
            fdm_port = 9002 + (index - 1) * 10
            include_cam = not (definition.disable_servicer_camera and index > 1)
            vehicle_sdf = GazeboVehicleModelBuilder().build(
                definition.drone, definition.camera, payload_slots=definition.phase4_payload_slots,
                fdm_port=fdm_port, vehicle_id=vehicle_id,
                include_camera=include_cam,
            )
            model_dir = run_dir / "models" / vehicle_id
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "model.sdf").write_text(vehicle_sdf, encoding="utf-8")
            (model_dir / "model.config").write_text(
                f"<?xml version='1.0'?><model><name>{vehicle_id}</name><version>1.0</version><sdf version='1.9'>model.sdf</sdf><description>Generated Stage 13SIM reference vehicle</description></model>\n",
                encoding="utf-8",
            )
        survivor_assets = Path(__file__).resolve().parents[3] / "simulation" / "assets" / "survivors"
        packaged_survivors = run_dir / "models" / "survivors"
        packaged_survivors.mkdir(parents=True)
        for asset_name in ("CesiumMan.glb", "CesiumMan.LICENSE.md", "manifest.yaml"):
            shutil.copy2(survivor_assets / asset_name, packaged_survivors / asset_name)
        (packaged_survivors / "model.config").write_text(
            "<?xml version='1.0'?><model><name>survivors</name><version>1.0</version>"
            "<sdf version='1.9'>model.sdf</sdf><description>CC-BY-4.0 CesiumMan simulation asset package.</description></model>\n",
            encoding="utf-8",
        )
        (packaged_survivors / "model.sdf").write_text(
            "<?xml version='1.0'?><sdf version='1.9'><model name='survivors'><static>true</static>"
            "<link name='asset'><visual name='visual'><geometry><mesh><uri>CesiumMan.glb</uri></mesh></geometry>"
            "</visual></link></model></sdf>\n",
            encoding="utf-8",
        )
        gazebo_actor_source = Path(__file__).resolve().parents[3] / "simulation" / "assets" / "gazebo_actor"
        packaged_actor = run_dir / "models" / "gazebo_actor"
        (packaged_actor / "meshes").mkdir(parents=True)
        for asset_name in ("stand.dae", "sitting.dae"):
            shutil.copy2(gazebo_actor_source / "meshes" / asset_name, packaged_actor / "meshes" / asset_name)
        for asset_name in ("LICENSE.md", "manifest.yaml"):
            shutil.copy2(gazebo_actor_source / asset_name, packaged_actor / asset_name)
        for name in ("autopilot_estimate.jsonl", "events.jsonl"):
            writer.write_text(name, "")
        self.definition, self.run_dir = definition, run_dir
        self.state = SimulationState.READY
        return run_dir

    def restore_prepared_run(self, mission_id: str) -> bool:
        """Recover the newest generated run for one mission after backend restart."""
        if self.state not in {SimulationState.IDLE, SimulationState.COMPLETED, SimulationState.FAILED, SimulationState.READY} or not self.runs_root.is_dir():
            return False
        if self.processes:
            try:
                self.processes.stop()
            except Exception:
                pass
            self.processes = None
        if self.adapter:
            try:
                self.adapter.disconnect()
            except Exception:
                pass
            self.adapter = None
        candidates = sorted(
            (path for path in self.runs_root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for run_dir in candidates:
            definition_path = run_dir / "simulation_run_definition.json"
            if not (run_dir / "generated_world.sdf").is_file():
                continue
            try:
                if definition_path.is_file():
                    definition = SimulationRunDefinition.model_validate_json(definition_path.read_text(encoding="utf-8"))
                else:
                    # Runs generated before restart recovery did not retain the
                    # request. Reconstruct only the launcher definition from
                    # their local simulation artifacts; this does not expose
                    # truth through any autonomy-facing API.
                    config = SimulationConfig.model_validate(json.loads((run_dir / "simulation_config.json").read_text(encoding="utf-8")))
                    georef = json.loads((run_dir / "georeference.json").read_text(encoding="utf-8"))
                    origin = tuple(georef["origin_wgs84"])
                    truth = json.loads((run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").read_text(encoding="utf-8"))
                    survivors = SurvivorScenario.model_validate({
                        "scenario_id": config.survivor_scenario_id,
                        "survivors": [
                            {
                                "survivor_id": item["survivor_id"],
                                "latitude": item["position_wgs84"][0], "longitude": item["position_wgs84"][1],
                                "up_m": 0.0, "body_state": item.get("body_state", "standing"),
                                "enabled": item.get("enabled", True),
                            }
                            for item in truth.get("survivors", [])
                        ],
                    })
                    mission = MissionWorldProfile.model_validate({
                        "mission_id": config.mission_world_profile_id,
                        "boundary_wgs84": [(origin[0], origin[1])] * 3,
                        "origin_wgs84": origin,
                        "ground_elevation_m": {"value": 0, "unit": "m", "provenance": "SIMULATION_ONLY"},
                        "world_dimensions_m": {"value": [4000, 4000, 80], "unit": "m", "provenance": "SIMULATION_ONLY"},
                        "launch_zone_width_m": {"value": 3.6576, "unit": "m", "provenance": "SIMULATION_ONLY"},
                        "launch_zone_length_m": {"value": 3.6576, "unit": "m", "provenance": "SIMULATION_ONLY"},
                        "environment_profile_id": config.environment_profile_id,
                    })
                    definition = SimulationRunDefinition(
                        run_id=run_dir.name, config=config,
                        drone=DroneProfile.model_validate_json((run_dir / "drone_profile.json").read_text(encoding="utf-8")),
                        camera=CameraProfile.model_validate_json((run_dir / "camera_profile.json").read_text(encoding="utf-8")),
                        environment=EnvironmentProfile.model_validate_json((run_dir / "environment_profile.json").read_text(encoding="utf-8")),
                        mission=mission, survivors=survivors,
                        sensor_profile=SensorRealismProfile.model_validate_json((run_dir / "sensor_profile.json").read_text(encoding="utf-8")),
                        sensor_seeds=SensorSeeds.model_validate_json((run_dir / "sensor_seed.json").read_text(encoding="utf-8")),
                        fleet_count=max(1, len(list((run_dir / "models").glob("drone_*")))),
                    )
                    definition_path.write_text(definition.model_dump_json(indent=2), encoding="utf-8")
            except Exception:
                continue
            if definition.mission.mission_id != mission_id:
                continue
            self.definition, self.run_dir = definition, run_dir
            self.error = None
            self.readiness = {"restored_prepared_run": True, "run_id": definition.run_id}
            self.state = SimulationState.READY
            return True
        return False

    def start(self) -> None:
        if self.state != SimulationState.READY or self.run_dir is None:
            raise RuntimeError("simulation must be generated and READY before start")
        self.state = SimulationState.STARTING_GAZEBO
        try:
            self.processes = self.launcher.start(self.run_dir, fleet_count=self.definition.fleet_count if self.definition else 1)
            import time
            time.sleep(10)
            self.state = SimulationState.WAITING_FOR_READY
            assert self.definition is not None
            camera_topic = self.launcher.wait_for_topic(
                f"/drone_01/{self.definition.camera.camera_id}/image",
                timeout_s=60,
            )
            self.adapter = SITLAutopilotAdapter(
                self.launcher.connection,
                environment="sitl",
                command_authority="simulation_only",
                heartbeat_timeout_s=120,
                command_timeout_s=15,
            )
            identity = self.adapter.connect(
                check_alive=lambda: self.processes is not None and self.processes.running("arducopter")
            )
            if int(identity["system_id"]) != 1:
                raise RuntimeError(f"unexpected Stage 13SIM SYSID: {identity['system_id']}")
            # With --no-mavproxy ArduPilot initially streams little more than
            # HEARTBEAT.  Request normalized estimated-state telemetry before
            # readiness is evaluated, exactly as the established Stage 3
            # runner does.  These are MAVLink estimates, never Gazebo truth.
            self.adapter.request_message_rates({
                "GPS_RAW_INT": 5.0,
                "RAW_IMU": 20.0,
                "SCALED_PRESSURE": 10.0,
                "GLOBAL_POSITION_INT": 10.0,
                "LOCAL_POSITION_NED": 10.0,
                "HOME_POSITION": 1.0,
                "SYS_STATUS": 2.0,
                "EKF_STATUS_REPORT": 2.0,
                "ATTITUDE": 10.0,
                "ATTITUDE_QUATERNION": 10.0,
                "SERVO_OUTPUT_RAW": 10.0,
                "MISSION_CURRENT": 2.0,
                "MISSION_ITEM_REACHED": 2.0,
                "EXTENDED_SYS_STATE": 2.0,
            })
            firmware = self.adapter.firmware_version()
            self.readiness = {"gazebo": True, "camera_topic": camera_topic, "ardupilot": {**identity, "firmware": firmware}, "sysid_verified": True, "georeference": True}
            self.clock.real_time_factor = self.definition.environment.real_time_factor if self.definition else 1.0
            self.clock.start()
            self.state = SimulationState.RUNNING
        except Exception as reason:
            self.error = str(reason)
            self.state = SimulationState.FAILED
            if self.adapter:
                self.adapter.disconnect()
                self.adapter = None
            if self.processes:
                self.processes.stop()
                self.processes = None
            raise

    def stop(self, completed: bool = False) -> None:
        if self.state in {SimulationState.IDLE, SimulationState.COMPLETED}:
            return
        self.state = SimulationState.STOPPING
        if self.adapter:
            self.adapter.disconnect()
            self.adapter = None
        if self.processes:
            self.processes.stop()
            self.processes = None
        self.state = SimulationState.COMPLETED if completed else SimulationState.READY

    def close(self) -> None:
        """Idempotent application-shutdown hook for every owned child process."""
        if self.processes:
            if self.adapter:
                self.adapter.disconnect()
                self.adapter = None
            self.processes.stop()
            self.processes = None
        if self.state not in {SimulationState.IDLE, SimulationState.COMPLETED}:
            self.state = SimulationState.COMPLETED

    def status(self, include_truth: bool = False) -> dict[str, object]:
        value: dict[str, object] = {
            "state": self.state,
            "run_id": self.definition.run_id if self.definition else None,
            "execution_source": "SITL_GAZEBO",
            "simulation_lab": True,
            "no_physical_aircraft": True,
            "survivor_count": len(self.definition.survivors.survivors) if self.definition else 0,
            "config_locked": self.state in {SimulationState.STARTING_GAZEBO, SimulationState.STARTING_SITL, SimulationState.WAITING_FOR_READY, SimulationState.RUNNING, SimulationState.PAUSED, SimulationState.STOPPING},
            "sensor_realism": (
                self.definition.sensor_profile.model_dump(mode="json") if self.definition else None
            ),
            "error": self.error,
            "readiness": self.readiness,
        }
        # This is intentionally sourced only from the existing MAVLink router.
        # Gazebo truth is never mixed into the normal Simulation Lab status.
        if self.adapter and self.adapter.router and self.definition:
            position = self.adapter.router.latest("GLOBAL_POSITION_INT")
            attitude = self.adapter.router.latest("ATTITUDE_QUATERNION")
            velocity = self.adapter.router.latest("LOCAL_POSITION_NED")
            heartbeat = self.adapter.router.latest("HEARTBEAT")
            if position is not None and attitude is not None:
                origin = GeoPoint(
                    latitude=self.definition.mission.origin_wgs84[0],
                    longitude=self.definition.mission.origin_wgs84[1],
                    altitude_m=self.definition.mission.origin_wgs84[2],
                )
                frame = SimulationGeoreference(origin).local
                enu = frame.to_enu(GeoPoint(
                    latitude=int(position.lat) / 1e7,
                    longitude=int(position.lon) / 1e7,
                    altitude_m=(origin.altitude_m or 0.0) + int(position.relative_alt) / 1000,
                ))
                value["estimated_vehicle"] = {
                    "classification": "AUTOPILOT_ESTIMATE",
                    "position_enu_m": [enu.east_m, enu.north_m, enu.up_m],
                    "position_wgs84": [int(position.lat) / 1e7, int(position.lon) / 1e7],
                    "quaternion_body_to_ned": [float(attitude.q1), float(attitude.q2), float(attitude.q3), float(attitude.q4)],
                    "velocity_ned_mps": (
                        [float(velocity.vx), float(velocity.vy), float(velocity.vz)]
                        if velocity is not None else [int(position.vx) / 100, int(position.vy) / 100, int(position.vz) / 100]
                    ),
                    "relative_altitude_m": int(position.relative_alt) / 1000,
                    "armed": bool(heartbeat and int(heartbeat.base_mode) & 128),
                    "source": "MAVLINK_ESTIMATE_ONLY",
                }
        if include_truth and self.run_dir:
            truth_path = self.run_dir / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json"
            value["ground_truth"] = json.loads(truth_path.read_text(encoding="utf-8"))
            if self.adapter and self.adapter.router:
                gps = self.adapter.router.latest("GPS_RAW_INT")
                imu = self.adapter.router.latest("RAW_IMU")
                baro = self.adapter.router.latest("SCALED_PRESSURE")
                value["sensor_measurement"] = {
                    "classification": "SIMULATED_SENSOR_MEASUREMENT_OBSERVER_ONLY",
                    "gps": ({
                        "latitude": int(gps.lat) / 1e7,
                        "longitude": int(gps.lon) / 1e7,
                        "altitude_msl_m": int(gps.alt) / 1000,
                        "fix_type": int(gps.fix_type),
                    } if gps is not None else None),
                    "imu": ({
                        "accel_mg": [int(imu.xacc), int(imu.yacc), int(imu.zacc)],
                        "gyro_millirad_s": [int(imu.xgyro), int(imu.ygyro), int(imu.zgyro)],
                        "mag_mgauss": [int(imu.xmag), int(imu.ymag), int(imu.zmag)],
                    } if imu is not None else None),
                    "barometer": ({
                        "absolute_pressure_hpa": float(baro.press_abs),
                        "differential_pressure_hpa": float(baro.press_diff),
                    } if baro is not None else None),
                }
        return value
