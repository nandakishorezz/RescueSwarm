"""Phase-1 fleet boundaries; no external SITL/Gazebo process in this suite."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ground.backend.app.multidrone.contracts import (
    DroneInstance, DroneRole, FleetConfiguration, FleetSession,
    SurvivorObservation, default_configuration,
)
from ground.backend.app.multidrone.runtime import MultiDroneRuntime, fleet_rtl_budget_s
from ground.backend.app.sitl.surveillance_service import SurveillanceService
from ground.backend.app.sitl.runner import SITLMissionRunner
from ground.backend.app.fleet.registry import SharedTargetRegistry
from ground.backend.app.services.telemetry_service import RealtimeEnvelope


def test_default_roster_has_separate_roles_sysids_and_spawns():
    config = default_configuration("mission")
    assert [item.role for item in config.drones] == [DroneRole.SEARCHER, DroneRole.SERVICER]
    assert [item.system_id for item in config.drones] == [1, 2]
    assert [item.spawn_north_m for item in config.drones] == [0, 2]


@pytest.mark.parametrize("edit", [
    {"vehicle_id": "drone_01"}, {"system_id": 1}, {"instance_id": 0},
    {"spawn_north_m": 0}, {"spawn_heading_deg": 0},
])
def test_invalid_second_vehicle_identity_or_spawn_rejected(edit):
    drones = [item.model_dump() for item in default_configuration("m").drones]
    drones[1].update(edit)
    with pytest.raises(ValidationError):
        FleetConfiguration(mission_id="m", drones=drones)


def test_ports_are_unique_and_occupied_ports_fail_closed(monkeypatch):
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.simulation_lab = type("Lab", (), {"processes": None})()
    runtime.sitl_manager = type("Manager", (), {"tasks": {}})()
    checked = []
    def in_use(port):
        checked.append(port)
        return port == 45679
    monkeypatch.setattr(runtime, "_port_in_use", in_use)
    with pytest.raises(RuntimeError, match="45679"):
        runtime._check_resources(2)
    assert len(checked) == len(set(checked))
    assert {5760, 5770, 5761, 45678, 45679}.issubset(set(checked))


def test_searcher_rejected_and_servicer_cannot_dispatch_without_coordinator():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.session = FleetSession(
        session_id=uuid4(), mission_id="m", run_id="r", state="SEARCHING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {item.vehicle_id: {} for item in runtime.session.configuration.drones}
    with pytest.raises(PermissionError, match="SEARCHER"):
        runtime.authorize_service("drone_01")
    with pytest.raises(RuntimeError, match="Phase-2"):
        runtime.authorize_service("drone_02")


def test_fleet_camera_frame_ids_and_ports_are_isolated(tmp_path):
    first = SurveillanceService(tmp_path, None, port=45678, vehicle_id="drone_01", precise_helper_stop=True)
    second = SurveillanceService(tmp_path, None, port=45679, vehicle_id="drone_02", precise_helper_stop=True)
    first.frame_count = second.frame_count = 7
    assert first._frame_id() != second._frame_id()
    assert first.helper_pid_file != second.helper_pid_file
    assert SurveillanceService(tmp_path, None)._frame_id() == "camera:0"  # single-drone contract


def test_observations_carry_provenance_but_do_not_create_service_jobs():
    session = uuid4()
    item = SurvivorObservation(
        session_id=session, mission_id="m", vehicle_id="drone_01",
        frame_id="drone_01:camera:7", observed_at=datetime.now(UTC),
        class_id=0, confidence=.8, bbox_xywh=(1, 2, 30, 40),
    )
    assert item.vehicle_id in item.frame_id
    # Phase 1 never promotes a raw observation to the shared confirmed registry.
    registry = SharedTargetRegistry("m")
    assert registry.all() == []
    assert item.model_dump()["session_id"] == session


def test_session_scoped_observations_do_not_leak_after_reset():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    old = uuid4()
    runtime.session = FleetSession(
        session_id=old, mission_id="m", run_id="r", state="SEARCHING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.observations = []
    observation = type("Observation", (), {"frame_id": "drone_01:camera:1", "target_track_id": "t"})()
    runtime._observation(old, "drone_01", observation, None)
    assert len(runtime.observations) == 1
    runtime.session.session_id = uuid4()
    runtime.observations = []
    runtime._observation(old, "drone_01", observation, None)
    assert runtime.observations == []


def test_stopped_fleet_preserves_real_camera_counters_as_stale():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.session = FleetSession(
        session_id=uuid4(), mission_id="m", run_id="r", state="STOPPED",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {"drone_01": {"vehicle_id": "drone_01", "last_update_monotonic": None}}
    runtime.cameras = {}
    runtime.camera_history = {"drone_01": {
        "camera_frame_count": 42, "camera_decoded_count": 42, "camera_inference_count": 41,
        "camera_last_frame_at": 1.0, "camera_model_status": "READY",
    }}
    runtime.events = []
    runtime.observations = []
    runtime.error = None
    runtime.plan = None
    status = runtime.status()
    assert status["vehicles"][0]["camera_status"] == "STALE"
    assert status["vehicles"][0]["camera_frame_count"] == 42


def test_preflight_abort_never_connects_or_arms():
    from ground.backend.tests.test_stage9s_fleet import boundary, global_plan

    class Adapter:
        connection_string = "tcp:127.0.0.1:5760"
        def __init__(self):
            self.connected = False
            self.armed = False
        def connect(self):
            self.connected = True
            return {"system_id": 1}
        def arm(self):
            self.armed = True
        def disconnect(self):
            pass

    adapter = Adapter()
    runner = SITLMissionRunner(adapter, 1, 1, 1, {}, vehicle_id="drone_01")
    runner.abort()
    result = runner.execute(global_plan(), boundary())
    assert result.state.value == "ABORTED"
    assert not adapter.connected and not adapter.armed


def test_returning_one_vehicle_does_not_abort_other():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    first = type("Runner", (), {"abort_requested": False, "abort": lambda self: setattr(self, "abort_requested", True)})()
    second = type("Runner", (), {"abort_requested": False, "abort": lambda self: setattr(self, "abort_requested", True)})()
    runtime.runners = {"drone_01": first, "drone_02": second}
    runtime.vehicles = {"drone_01": {"state": "SEARCHING"}, "drone_02": {"state": "SEARCHING"}}
    runtime.status = lambda: {"vehicles": runtime.vehicles}
    runtime.stop_vehicle("drone_01")
    assert first.abort_requested
    assert not second.abort_requested
    assert runtime.vehicles["drone_02"]["state"] == "SEARCHING"


def test_direct_service_api_enforces_role_even_without_ui(client):
    runtime = client.app.state.multidrone_runtime
    runtime.session = FleetSession(
        session_id=uuid4(), mission_id="m", run_id="r", state="SEARCHING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {item.vehicle_id: {} for item in runtime.session.configuration.drones}
    assert client.post("/api/multidrone/vehicles/drone_01/service").status_code == 403
    response = client.post("/api/multidrone/vehicles/drone_02/service")
    assert response.status_code == 409
    assert "Phase-2" in response.json()["detail"]


def test_mavlink_wrong_sysid_fails_before_arm():
    from ground.backend.tests.test_stage9s_fleet import boundary, global_plan

    class Adapter:
        connection_string = "tcp:127.0.0.1:5770"
        last_verification = None
        def __init__(self):
            self.armed = False
        def connect(self):
            return {"system_id": 1}
        def arm(self):
            self.armed = True
        def disconnect(self):
            pass

    adapter = Adapter()
    runner = SITLMissionRunner(adapter, 1, 1, 1, {}, vehicle_id="drone_02", expected_system_id=2)
    result = runner.execute(global_plan(), boundary())
    assert result.state.value == "FAILED"
    assert "expected MAVLink SYSID 2" in result.errors[0]
    assert not adapter.armed


def test_fleet_session_uses_existing_websocket_manager_without_mission_id_spoofing(client):
    runtime = client.app.state.multidrone_runtime
    runtime.session = FleetSession(
        session_id=uuid4(), mission_id="database-mission", run_id="r", state="CONNECTING",
        configuration=default_configuration("database-mission"), started_at=datetime.now(UTC),
    )
    with client.websocket_connect(f"/ws/mission/{runtime.session.session_id}") as socket:
        snapshot = socket.receive_json()
        assert snapshot["type"] == "system.snapshot"
        assert snapshot["payload"]["status"] == "FLEET_SESSION"
        frame = RealtimeEnvelope(
            type="camera_frame", mission_id=str(runtime.session.session_id),
            vehicle_id="drone_02", sequence=1,
            payload={"frame_id": "drone_02:camera:1", "image_b64": "test"},
        )
        client.portal.call(client.app.state.websocket_manager.broadcast, frame)
        received = socket.receive_json()
        assert received["vehicle_id"] == "drone_02"
        assert received["payload"]["frame_id"] == "drone_02:camera:1"


def test_fleet_rtl_budget_uses_measured_camera_throughput_and_stays_bounded():
    assert fleet_rtl_budget_s(180, .2) == 360
    assert fleet_rtl_budget_s(180, 1) == 180
    assert fleet_rtl_budget_s(180, .02) == 600
    with pytest.raises(ValueError):
        fleet_rtl_budget_s(180, 0)


def test_real_disarm_event_updates_final_read_model():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m", run_id="r", state="STOPPING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {"drone_01": {"telemetry": {"armed": True}, "last_event": None}}
    runtime.events = []
    event = type("Event", (), {"model_dump": lambda self, **kwargs: {"type": "VEHICLE_DISARMED"}})()
    runtime._event(session_id, "drone_01", event)
    assert runtime.vehicles["drone_01"]["telemetry"]["armed"] is False


def test_backend_trails_are_vehicle_keyed_and_ignore_replayed_position():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m", run_id="r", state="CONNECTING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.vehicles = {vehicle: {} for vehicle in ("drone_01", "drone_02")}
    runtime.trails = {vehicle: [] for vehicle in runtime.vehicles}
    runtime.cameras = {}

    class Vehicle:
        def __init__(self, lat):
            self.latitude = lat
            self.longitude = 77.5
        def model_dump(self, **kwargs):
            return {"latitude": self.latitude, "longitude": self.longitude}

    execution = type("Execution", (), {
        "state": type("State", (), {"value": "SEARCHING"})(),
        "actual_coverage_percent": 2.0, "current_mission_item": 3, "total_mission_items": 12,
    })()
    runtime._telemetry(session_id, "drone_01", type("Snapshot", (), {"vehicle": Vehicle(12.1)})(), execution)
    runtime._telemetry(session_id, "drone_01", type("Snapshot", (), {"vehicle": Vehicle(12.1)})(), execution)
    runtime._telemetry(session_id, "drone_02", type("Snapshot", (), {"vehicle": Vehicle(12.2)})(), execution)
    assert len(runtime.trails["drone_01"]) == len(runtime.trails["drone_02"]) == 1
    assert runtime.trails["drone_01"][0] != runtime.trails["drone_02"][0]


def test_idle_status_schema_matches_frontend_contract():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.session = None
    status = runtime.status()
    assert status["state"] == "IDLE"
    assert status["session"] is None
    assert isinstance(status["vehicles"], list)
    assert isinstance(status["targets"], list)
    assert isinstance(status["assignments"], list)
    assert isinstance(status["deliveries"], list)
    assert isinstance(status["events"], list)
    assert isinstance(status["observations"], list)
    assert isinstance(status["trails"], dict)
    assert status["error"] is None
    assert status["coordination_state"] == "PHASE_2_NOT_IMPLEMENTED"
