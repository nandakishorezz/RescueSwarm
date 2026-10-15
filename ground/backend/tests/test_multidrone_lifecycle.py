"""Automated tests for Phase 1.1 fleet lifecycle state machine."""

import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ground.backend.app.multidrone.contracts import (
    DroneInstance, DroneRole, FleetConfiguration, FleetSession, default_configuration,
)
from ground.backend.app.multidrone.runtime import MultiDroneRuntime
from ground.backend.app.multidrone.state_machine import (
    DroneLifecycleState,
    DroneStateMachine,
    FleetState,
    FleetStateMachine,
    VALID_FLEET_TRANSITIONS,
    VALID_DRONE_TRANSITIONS,
)


def test_fleet_state_machine_valid_and_invalid_transitions():
    sm = FleetStateMachine(FleetState.IDLE)
    assert sm.current == FleetState.IDLE
    assert sm.reason == "No active fleet session"

    # Valid transitions: IDLE -> VALIDATING -> STARTING -> WAITING_FOR_TELEMETRY -> RUNNING
    sm.transition(FleetState.VALIDATING)
    assert sm.current == FleetState.VALIDATING
    assert sm.reason == "Checking configuration"

    sm.transition(FleetState.STARTING)
    assert sm.current == FleetState.STARTING
    assert sm.reason == "Launching vehicles"

    sm.transition(FleetState.WAITING_FOR_TELEMETRY)
    assert sm.current == FleetState.WAITING_FOR_TELEMETRY
    assert sm.reason == "Waiting for vehicle connections"

    sm.transition(FleetState.RUNNING)
    assert sm.current == FleetState.RUNNING
    assert sm.reason == "Fleet active"

    # RUNNING -> DEGRADED -> RUNNING (recovery)
    sm.transition(FleetState.DEGRADED, "Vehicle telemetry stale")
    assert sm.current == FleetState.DEGRADED
    assert sm.reason == "Vehicle telemetry stale"

    sm.transition(FleetState.RUNNING, "Fleet active")
    assert sm.current == FleetState.RUNNING

    # RUNNING -> RETURNING -> STOPPING -> STOPPED -> IDLE
    sm.transition(FleetState.RETURNING)
    assert sm.current == FleetState.RETURNING
    assert sm.reason == "Vehicles returning safely"

    sm.transition(FleetState.STOPPING)
    assert sm.current == FleetState.STOPPING
    assert sm.reason == "Cleaning up simulation resources"

    sm.transition(FleetState.STOPPED)
    assert sm.current == FleetState.STOPPED
    assert sm.reason == "Fleet stopped"

    sm.transition(FleetState.IDLE)
    assert sm.current == FleetState.IDLE

    # Invalid transitions should raise ValueError
    with pytest.raises(ValueError, match="Invalid fleet state transition"):
        sm.transition(FleetState.RUNNING)

    with pytest.raises(ValueError, match="Invalid fleet state transition"):
        sm.transition(FleetState.STOPPING)


def test_drone_state_machine_valid_and_invalid_transitions():
    dsm = DroneStateMachine("drone_01", DroneLifecycleState.CONFIGURED)
    assert dsm.current == DroneLifecycleState.CONFIGURED

    # CONFIGURED -> STARTING -> WAITING_FOR_HEARTBEAT -> READY -> SEARCHING
    dsm.transition(DroneLifecycleState.STARTING)
    assert dsm.current == DroneLifecycleState.STARTING

    dsm.transition(DroneLifecycleState.WAITING_FOR_HEARTBEAT)
    assert dsm.current == DroneLifecycleState.WAITING_FOR_HEARTBEAT

    dsm.transition(DroneLifecycleState.READY)
    assert dsm.current == DroneLifecycleState.READY

    dsm.transition(DroneLifecycleState.SEARCHING)
    assert dsm.current == DroneLifecycleState.SEARCHING

    # SEARCHING -> RETURNING -> LANDING -> LANDED -> DISARMED -> STOPPED
    dsm.transition(DroneLifecycleState.RETURNING)
    assert dsm.current == DroneLifecycleState.RETURNING

    dsm.transition(DroneLifecycleState.LANDING)
    assert dsm.current == DroneLifecycleState.LANDING

    dsm.transition(DroneLifecycleState.LANDED)
    assert dsm.current == DroneLifecycleState.LANDED

    dsm.transition(DroneLifecycleState.DISARMED)
    assert dsm.current == DroneLifecycleState.DISARMED

    dsm.transition(DroneLifecycleState.STOPPED)
    assert dsm.current == DroneLifecycleState.STOPPED

    # Invalid transition
    with pytest.raises(ValueError, match="Invalid drone state transition"):
        dsm.transition(DroneLifecycleState.SEARCHING)


def test_complete_idle_and_stopped_status_schemas():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.session = None
    runtime.events = []
    runtime.error = None
    runtime.vehicles = {}
    runtime.failed_vehicles = []

    # 1. IDLE status
    idle_status = runtime.status()
    expected_keys = {
        "state", "state_reason", "state_updated_at", "session", "vehicles",
        "failed_vehicles", "targets", "observations", "trails", "assignments",
        "deliveries", "events", "error", "partition", "coordination_state",
    }
    assert expected_keys.issubset(set(idle_status.keys()))
    assert idle_status["state"] == "IDLE"
    assert idle_status["session"] is None
    assert idle_status["vehicles"] == []
    assert idle_status["failed_vehicles"] == []

    # 2. STOPPED status
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m1", run_id="r1",
        state=FleetState.STOPPED.value,
        configuration=default_configuration("m1"),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    runtime.state_machine = FleetStateMachine(FleetState.STOPPED)
    runtime.vehicles = {
        "drone_01": {
            "vehicle_id": "drone_01", "role": "SEARCHER", "system_id": 1,
            "state": "STOPPED", "telemetry": None, "progress_percent": 100.0,
            "last_update_monotonic": None, "last_event": "VEHICLE_DISARMED",
        },
        "drone_02": {
            "vehicle_id": "drone_02", "role": "SERVICER", "system_id": 2,
            "state": "STOPPED", "telemetry": None, "progress_percent": 100.0,
            "last_update_monotonic": None, "last_event": "VEHICLE_DISARMED",
        },
    }
    stopped_status = runtime.status()
    assert expected_keys.issubset(set(stopped_status.keys()))
    assert stopped_status["state"] == "STOPPED"
    assert stopped_status["session"] is not None
    assert len(stopped_status["vehicles"]) == 2
    assert stopped_status["vehicles"][0]["state"] == "STOPPED"


def test_one_drone_failing_while_another_operational_transitions_to_degraded():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m1", run_id="r1",
        state=FleetState.RUNNING.value,
        configuration=default_configuration("m1"),
        started_at=datetime.now(UTC),
    )
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    now = time.monotonic()
    runtime.vehicles = {
        "drone_01": {
            "vehicle_id": "drone_01", "role": "SEARCHER", "system_id": 1,
            "state": DroneLifecycleState.SEARCHING.value,
            "telemetry": {"relative_altitude_m": 15.0, "armed": True},
            "last_update_monotonic": now,
        },
        "drone_02": {
            "vehicle_id": "drone_02", "role": "SERVICER", "system_id": 2,
            "state": DroneLifecycleState.SEARCHING.value,
            "telemetry": {"relative_altitude_m": 15.0, "armed": True},
            "last_update_monotonic": now,
        },
    }
    runtime.cameras = {}
    runtime.drone_state_machines = {
        "drone_01": DroneStateMachine("drone_01", DroneLifecycleState.SEARCHING),
        "drone_02": DroneStateMachine("drone_02", DroneLifecycleState.SEARCHING),
    }
    runtime.events = []
    runtime.failed_vehicles = []
    runtime.runners = {}

    # Initial check: fleet is RUNNING
    status = runtime.status()
    assert status["state"] == "RUNNING"
    assert status["failed_vehicles"] == []

    # Inject failure on drone_01 via failure event
    event = type("Event", (), {"model_dump": lambda self, **kwargs: {
        "type": "LINK_LOST", "message": "Connection lost to drone_01", "severity": "ERROR",
    }})()
    runtime._event(session_id, "drone_01", event)

    assert runtime.vehicles["drone_01"]["state"] == "FAILED"
    assert runtime.vehicles["drone_02"]["state"] == "SEARCHING"

    status = runtime.status()
    assert status["state"] == "DEGRADED"
    assert "drone_01" in status["failed_vehicles"]
    assert "Vehicle failed: drone_01" in status["state_reason"]


def test_stale_telemetry_and_recovery_after_telemetry_returns():
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m1", run_id="r1",
        state=FleetState.RUNNING.value,
        configuration=default_configuration("m1"),
        started_at=datetime.now(UTC),
    )
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    now = time.monotonic()
    runtime.vehicles = {
        "drone_01": {
            "vehicle_id": "drone_01", "role": "SEARCHER", "system_id": 1,
            "state": DroneLifecycleState.SEARCHING.value,
            "telemetry": {"relative_altitude_m": 15.0, "armed": True},
            "last_update_monotonic": now,
        },
        "drone_02": {
            "vehicle_id": "drone_02", "role": "SERVICER", "system_id": 2,
            "state": DroneLifecycleState.SEARCHING.value,
            "telemetry": {"relative_altitude_m": 15.0, "armed": True},
            "last_update_monotonic": now,
        },
    }
    runtime.cameras = {}
    runtime.drone_state_machines = {
        "drone_01": DroneStateMachine("drone_01", DroneLifecycleState.SEARCHING),
        "drone_02": DroneStateMachine("drone_02", DroneLifecycleState.SEARCHING),
    }
    runtime.events = []
    runtime.trails = {"drone_01": [], "drone_02": []}
    runtime.failed_vehicles = []
    runtime.runners = {}

    # 1. Fresh telemetry -> RUNNING
    assert runtime.status()["state"] == "RUNNING"

    # 2. Simulate stale telemetry on drone_02 (no updates for 5 seconds)
    runtime.vehicles["drone_02"]["last_update_monotonic"] = now - 5.0
    status = runtime.status()
    assert status["state"] == "DEGRADED"
    assert "drone_02" in status["failed_vehicles"]
    assert "Telemetry stale for drone_02" in status["state_reason"]

    # 3. Telemetry returns for drone_02 -> Fleet recovers to RUNNING
    class MockVehicle:
        latitude = 12.97
        longitude = 77.59
        flight_mode = "AUTO"
        armed = True
        relative_altitude_m = 15.0
        def model_dump(self, **kwargs):
            return {"latitude": self.latitude, "longitude": self.longitude, "flight_mode": self.flight_mode, "armed": self.armed, "relative_altitude_m": self.relative_altitude_m}

    mock_exec = type("Exec", (), {
        "actual_coverage_percent": 25.0, "current_mission_item": 5, "total_mission_items": 12,
        "state": type("State", (), {"value": "SEARCHING"})(), "errors": [], "rtl_completed": False,
    })()
    runtime._telemetry(session_id, "drone_02", type("Snap", (), {"vehicle": MockVehicle(), "landed_state": 0, "current_mission_item": 5})(), mock_exec)

    # Status should now be restored to RUNNING
    recovered_status = runtime.status()
    assert recovered_status["state"] == "RUNNING"
    assert recovered_status["failed_vehicles"] == []
    assert any(e["type"] == "FLEET_RECOVERED" for e in runtime.events)


def test_duplicate_start_prevented_while_active():
    import asyncio
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    runtime.session = FleetSession(
        session_id=uuid4(), mission_id="m", run_id="r", state="RUNNING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime._lock = asyncio.Lock()
    runtime.task = None

    with pytest.raises(RuntimeError, match="A fleet session is already active"):
        asyncio.run(runtime.start(default_configuration("m")))


def test_safe_return_progression():
    import asyncio
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    session_id = uuid4()
    runtime.session = FleetSession(
        session_id=session_id, mission_id="m", run_id="r", state="RUNNING",
        configuration=default_configuration("m"), started_at=datetime.now(UTC),
    )
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    runner1 = type("R", (), {"abort_requested": False, "abort": lambda self: setattr(self, "abort_requested", True)})()
    runner2 = type("R", (), {"abort_requested": False, "abort": lambda self: setattr(self, "abort_requested", True)})()
    runtime.runners = {"drone_01": runner1, "drone_02": runner2}
    runtime.vehicles = {
        "drone_01": {"state": "SEARCHING", "last_update_monotonic": time.monotonic()},
        "drone_02": {"state": "SEARCHING", "last_update_monotonic": time.monotonic()},
    }
    runtime.drone_state_machines = {
        "drone_01": DroneStateMachine("drone_01", DroneLifecycleState.SEARCHING),
        "drone_02": DroneStateMachine("drone_02", DroneLifecycleState.SEARCHING),
    }
    runtime.events = []
    runtime.cameras = {}
    runtime.failed_vehicles = []
    mock_task = type("Task", (), {"done": lambda self: False})()
    runtime.task = mock_task

    # Trigger operator stop
    status = asyncio.run(runtime.stop())
    assert runtime.stop_requested is True
    assert runner1.abort_requested is True
    assert runner2.abort_requested is True
    assert status["state"] == "RETURNING"
    assert status["vehicles"][0]["state"] == "RETURNING"
    assert status["vehicles"][1]["state"] == "RETURNING"
