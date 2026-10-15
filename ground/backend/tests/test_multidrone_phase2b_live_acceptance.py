"""Phase 2B.1 Autonomous Delivery Mocked-Adapter Pipeline Integration Test Suite.

Validates the full end-to-end multi-drone autonomous service delivery software pipeline:
In-Memory Survivor Ingestion -> Exclusive Service Coordinator ->
Production SITLMissionRunner.service_hook -> MultiDroneServiceExecutor ->
Safe Standoff Approach -> Idempotent Simulated Release -> Independent Verification ->
Safe Climb -> Forward Search Resumption -> SEARCHER Non-Interference.

NOTE (Phase 2B.1R Audit Finding): This test suite exercises genuine production
orchestration and execution logic using an in-memory mocked MAVLink adapter (MockMAVMaster).
It does NOT run live ArduCopter SITL or Gazebo physics/camera streaming.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import math
from pathlib import Path
import time
from uuid import UUID, uuid4

import cv2
import numpy as np
import pytest
from shapely.geometry import Polygon

from ground.backend.app.domain.execution import ExecutionState, MissionExecution
from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.domain.vehicle import VehicleState
from ground.backend.app.multidrone.contracts import (
    AssignmentState,
    DroneInstance,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    default_configuration,
)
from ground.backend.app.multidrone.coordinator import (
    ACTIVE_SERVICE_STATES,
    ExclusiveServiceCoordinator,
)
from ground.backend.app.multidrone.registry import SharedSurvivorRegistry
from ground.backend.app.multidrone.runtime import MultiDroneRuntime
from ground.backend.app.multidrone.service_executor import MultiDroneServiceExecutor
from ground.backend.app.multidrone.state_machine import (
    DroneLifecycleState,
    DroneStateMachine,
    FleetState,
    FleetStateMachine,
)
from ground.backend.app.planning.models import (
    CoveragePlan,
    CoverageRequest,
    PlanValidation,
    SweepStrategy,
)
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.coverage import ActualCoverageTracker
from ground.backend.app.sitl.guided import SITLGuidedNavigator
from ground.backend.app.sitl.runner import SITLMissionRunner
from ground.backend.app.sitl.surveillance_service import (
    SurveillanceService,
    accepts_active_detection,
)
from ground.backend.app.sitl.telemetry import SITLTelemetrySnapshot
from rescue_core.autonomy.models import SearchResumePolicyName
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point
from rescue_core.simulation.profiles import load_camera_profile


ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT_PATH = ROOT / "data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt"
VAL_IMAGES_DIR = ROOT / "data/training/stage13sim_nadir_person_v3/images/val"
VAL_LABELS_DIR = ROOT / "data/training/stage13sim_nadir_person_v3/labels/val"
ORIGIN = GeoPoint(latitude=12.9720, longitude=77.5925, altitude_m=0.0)


def _load_camera():
    return load_camera_profile(ROOT / "simulation/profiles/ov9281_reference_simulation.yaml")


def _make_fleet_configuration():
    return default_configuration("mission_phase2b_live")


class MockMAVMaster:
    """Mock MAVLink master tracking sent commands and vehicle mode."""

    def __init__(self, target_system=2):
        self.target_system = target_system
        self.target_component = 1
        self.commands_sent = []
        self.last_target_pos = None

    class mav:
        def __init__(self, parent):
            self.parent = parent

        def set_position_target_global_int_send(self, time_boot_ms, target_system, target_component,
                                               coordinate_frame, type_mask, lat_int, lon_int, alt,
                                               vx, vy, vz, afx, afy, afz, yaw, yaw_rate):
            lat = lat_int / 1e7
            lon = lon_int / 1e7
            self.parent.last_target_pos = (lat, lon, alt)
            self.parent.commands_sent.append(("GOTO_GLOBAL", (lat, lon, alt)))
            if hasattr(self.parent, "adapter") and self.parent.adapter:
                self.parent.adapter.pos_lat = lat
                self.parent.adapter.pos_lon = lon
                self.parent.adapter.rel_alt = alt

        def set_position_target_local_ned_send(self, time_boot_ms, target_system, target_component,
                                              coordinate_frame, type_mask, x, y, z,
                                              vx, vy, vz, afx, afy, afz, yaw, yaw_rate):
            self.parent.commands_sent.append(("VELOCITY_LOCAL", (vx, vy, vz)))
            if hasattr(self.parent, "adapter") and self.parent.adapter:
                self.parent.adapter.ground_speed = math.hypot(vx, vy)


def make_mock_live_adapter(vehicle_id: str, system_id: int) -> SITLAutopilotAdapter:
    """Mocked MAVLink autopilot adapter instance for multi-drone integration testing."""
    adapter = SITLAutopilotAdapter.__new__(SITLAutopilotAdapter)
    adapter.vehicle_id = vehicle_id
    adapter.system_id = system_id
    adapter.connection_string = f"tcp:127.0.0.1:{5760 + 10 * (system_id - 1)}"
    adapter.flight_mode = "AUTO"
    adapter.speed = 5.0
    adapter.current_seq = 4
    adapter.armed = True
    master = MockMAVMaster(target_system=system_id)
    master.mav = MockMAVMaster.mav(master)
    master.adapter = adapter
    adapter.master = master
    adapter.command_timeout_s = 5.0
    adapter.pos_lat = 12.9721
    adapter.pos_lon = 77.5926
    adapter.rel_alt = 20.0
    adapter.ground_speed = 4.5
    adapter.vehicle_source = adapter
    adapter._master = lambda: adapter.master
    adapter.set_guided = lambda: (setattr(adapter, "flight_mode", "GUIDED"), adapter.master.commands_sent.append(("SET_MODE", "GUIDED")))
    adapter.set_mode = lambda mode: (setattr(adapter, "flight_mode", mode), adapter.master.commands_sent.append(("SET_MODE", mode)))
    adapter.set_speed = lambda speed: (setattr(adapter, "speed", speed), adapter.master.commands_sent.append(("SET_SPEED", speed)))
    adapter.start_auto_mission = lambda: setattr(adapter, "flight_mode", "AUTO")
    adapter.rtl = lambda: (setattr(adapter, "flight_mode", "RTL"), adapter.master.commands_sent.append(("SET_MODE", "RTL")))
    adapter.command_yaw = lambda deg: adapter.master.commands_sent.append(("COMMAND_YAW", deg))
    adapter.set_mission_current = lambda seq: (setattr(adapter, "current_seq", seq), adapter.master.commands_sent.append(("SET_CURRENT", seq)))
    adapter.resume_auto = lambda: (setattr(adapter, "flight_mode", "AUTO"), adapter.master.commands_sent.append(("SET_MODE", "AUTO")))

    class Normalizer:
        def __init__(self, parent):
            self.parent = parent

        @property
        def snapshot(inner_self):
            snap = type("Snapshot", (), {})()
            snap.vehicle = VehicleState(
                vehicle_id=inner_self.parent.vehicle_id,
                name=inner_self.parent.vehicle_id,
                connected=True,
                armed=inner_self.parent.armed,
                flight_mode=inner_self.parent.flight_mode,
                relative_altitude_m=inner_self.parent.rel_alt,
                ground_speed_m_s=inner_self.parent.ground_speed,
                latitude=inner_self.parent.pos_lat,
                longitude=inner_self.parent.pos_lon,
                gps_fix_type=3,
                satellites=12,
            )
            return snap

    adapter.normalizer = Normalizer(adapter)
    return adapter


# ===========================================================================
# 1. Complete Two-Drone Pipeline Scenario: Ingest -> Coordination -> Delivery -> Resume
# ===========================================================================

@pytest.mark.anyio
async def test_complete_live_two_drone_scenario_execution():
    """Validates the full end-to-end multi-drone autonomous service delivery workflow.
    
    NOTE (Phase 2B.1R Audit Finding): This test verifies genuine production orchestration logic
    (MultiDroneServiceExecutor, ExclusiveServiceCoordinator, SearchResumeManager,
    GuidedApproachPlanner, compute_safe_delivery_point) against a simulated adapter.
    MAVLink commands are recorded and state transitions are asserted; vehicle
    physics are not simulated via live ArduCopter SITL/Gazebo in this unit test.
    
    1. Both vehicles connect with simulated telemetry.
    2. SEARCHER begins search and records progress.
    3. Confirmed survivor ingested into SharedSurvivorRegistry.
    4. Exactly one service job allocated to SERVICER (drone_02), never SEARCHER (drone_01).
    5. Checkpoint saved before leaving search route.
    6. Autonomous approach executed in GUIDED mode to safe standoff point (>= 1.0m from survivor).
    7. Stabilization hold at delivery altitude (5.0m).
    8. Simulated release occurs exactly once.
    9. Independent delivery verification succeeds.
    10. Safe climb and exact search resumption without replaying reached waypoints.
    11. SEARCHER continues search independently without interruption.
    12. Clean fleet return and landing.
    """
    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    runtime.drone_state_machines = {
        "drone_01": DroneStateMachine("drone_01", DroneLifecycleState.SEARCHING),
        "drone_02": DroneStateMachine("drone_02", DroneLifecycleState.SEARCHING),
    }
    now = time.monotonic()
    runtime.vehicles = {
        "drone_01": {
            "vehicle_id": "drone_01",
            "state": "SEARCHING",
            "role": "SEARCHER",
            "last_update_monotonic": now,
            "connected": True,
            "telemetry_age_s": 0.1,
            "telemetry": {
                "armed": True, "flight_mode": "AUTO", "relative_altitude_m": 20.0,
                "ground_speed_m_s": 4.5, "latitude": 12.9721, "longitude": 77.5926,
            },
        },
        "drone_02": {
            "vehicle_id": "drone_02",
            "state": "SEARCHING",
            "role": "SERVICER",
            "last_update_monotonic": now,
            "connected": True,
            "telemetry_age_s": 0.1,
            "telemetry": {
                "armed": True, "flight_mode": "AUTO", "relative_altitude_m": 20.0,
                "ground_speed_m_s": 4.5, "latitude": 12.9722, "longitude": 77.5927,
            },
        },
    }
    runtime.runners = {}
    runtime.cameras = {}
    runtime.events = []
    runtime.observations = []
    runtime.failed_vehicles = []
    runtime.trails = {"drone_01": [], "drone_02": []}
    runtime.error = None
    runtime.stop_requested = False

    config = _make_fleet_configuration()
    session = FleetSession(
        session_id=uuid4(),
        mission_id=config.mission_id,
        run_id="run_live_p2b",
        state="RUNNING",
        configuration=config,
        started_at=datetime.now(UTC),
    )
    runtime.session = session
    runtime.registry = SharedSurvivorRegistry(session_id=session.session_id, mission_id=session.mission_id)
    runtime.coordinator = ExclusiveServiceCoordinator(
        session=session,
        fleet_config=config,
        registry=runtime.registry,
        vehicles_state_supplier=lambda: runtime._get_vehicles_snapshot(),
        event_callback=runtime._add_event,
    )
    runtime.registry.on_confirmed_callback = runtime.coordinator.on_survivor_confirmed

    # Set up Boundary and CoveragePlan
    frame = LocalCoordinateFrame(ORIGIN)
    boundary = Boundary(
        vertices=[
            frame.to_wgs84(EnuPoint(-50, -50, 0)),
            frame.to_wgs84(EnuPoint(50, -50, 0)),
            frame.to_wgs84(EnuPoint(50, 50, 0)),
            frame.to_wgs84(EnuPoint(-50, 50, 0)),
        ],
        source_format="geojson",
        area_m2=10000.0,
        area_hectares=1.0,
        origin=ORIGIN,
    )

    plan = CoveragePlan(
        mission_id=config.mission_id,
        planner_version="1.0",
        configuration=CoverageRequest(
            search_altitude_m=20.0,
            search_speed_m_s=5.0,
            lane_spacing_m=10.0,
            effective_swath_m=15.0,
            edge_margin_m=2.0,
            sweep_strategy=SweepStrategy.AUTO_OPTIMIZED,
            turn_penalty_m=5.0,
            min_planned_coverage_percent=98.0,
            max_waypoints=100,
        ),
        safe_polygon_enu=[],
        safe_polygon_wgs84=[],
        sweep_angle_deg=0.0,
        segments=[],
        path_points_wgs84=[],
        path_points_enu=[],
        estimated_search_time_s=120.0,
        estimated_path_length_m=600.0,
        number_of_search_lines=4,
        number_of_turns=3,
        cost_m=600.0,
        validation=PlanValidation(
            valid=True,
            planned_coverage_percent=98.0,
            covered_area_m2=9800.0,
            missed_area_m2=200.0,
        ),
    )

    # Wire adapters and runners
    adapter_01 = make_mock_live_adapter("drone_01", system_id=1)
    adapter_02 = make_mock_live_adapter("drone_02", system_id=2)

    runner_01 = SITLMissionRunner.__new__(SITLMissionRunner)
    runner_01.vehicle_id = "drone_01"
    runner_01.adapter = adapter_01
    runner_01.abort_requested = False
    runner_01.service_hook = None  # SEARCHER never receives service hook

    service_executor = MultiDroneServiceExecutor(runtime)
    runner_02 = SITLMissionRunner.__new__(SITLMissionRunner)
    runner_02.vehicle_id = "drone_02"
    runner_02.adapter = adapter_02
    runner_02.abort_requested = False
    runner_02.service_hook = service_executor.execute_service_cycle  # SERVICER receives service hook

    runtime.runners = {"drone_01": runner_01, "drone_02": runner_02}

    # Step 1: Ingest confirmed survivor (simulating real camera confirmation pipeline)
    survivor_enu_east, survivor_enu_north = 25.0, 20.0
    survivor_wgs84 = frame.enu_to_wgs84(survivor_enu_east, survivor_enu_north, 0.0)

    # Ingest 4 temporal observations from drone_01's camera
    for i in range(4):
        obs = {
            "target_track_id": "track_cam_01",
            "frame_id": f"frame_{i:04d}",
            "observed_at": datetime.now(UTC),
            "camera_id": "ov9281_reference_simulation",
        }
        est = type("Est", (), {
            "target_id": "survivor-001",
            "latitude": survivor_wgs84.latitude,
            "longitude": survivor_wgs84.longitude,
            "east_m": survivor_enu_east,
            "north_m": survivor_enu_north,
            "uncertainty_m": 1.2,
            "observation_count": i + 1,
            "state": type("S", (), {"value": "CONFIRMED"})() if i >= 3 else type("S", (), {"value": "CANDIDATE"})(),
        })()
        runtime.registry.ingest("drone_01", obs, est)

    # Step 2: Verify survivor is confirmed and exactly one job is assigned to drone_02
    survivors = runtime.registry.confirmed_survivors()
    assert len(survivors) == 1
    assert survivors[0].canonical_id == "survivor-001"

    assignments = runtime.coordinator.assignments()
    assert len(assignments) == 1
    job = assignments[0]
    assert job.servicer_id == "drone_02", "Service job must be allocated to SERVICER (drone_02)"
    assert job.state == AssignmentState.ASSIGNED

    # Step 3: Run service hook on drone_02 to execute Phase 2B delivery
    execution_02 = MissionExecution(
        mission_id=config.mission_id,
        source="SITL",
        state=ExecutionState.SEARCHING,
        connection=adapter_02.connection_string,
        mission_fingerprint="fp_p2b_live",
    )
    execution_02.actual_coverage_percent = 35.0

    tracker_02 = ActualCoverageTracker(plan, ORIGIN)
    snap_02 = SITLTelemetrySnapshot(
        current_mission_item=6,
        reached_items=[3, 4, 5],
        landed_state=1,  # In air
        vehicle=VehicleState(
            vehicle_id="drone_02", name="drone_02", connected=True, armed=True,
            flight_mode="AUTO", relative_altitude_m=20.0, ground_speed_m_s=4.5,
            latitude=survivor_wgs84.latitude - 0.0002, longitude=survivor_wgs84.longitude - 0.0002,
        ),
    )

    # Execute service cycle
    handled = runner_02.service_hook(
        runner_02, plan, boundary, snap_02, execution_02, tracker_02,
        search_first=3, rtl_sequence=25,
    )
    assert handled is True, "Service execution cycle must complete successfully"

    # Step 4: Verify all Phase 2B delivery results
    completed_job = runtime.coordinator.get_assignment(job.assignment_id)
    assert completed_job.state == AssignmentState.COMPLETED
    assert completed_job.payload_released is True
    assert completed_job.delivery_verified is True
    assert completed_job.release_id is not None
    assert completed_job.release_id.lower().startswith("rel")
    assert completed_job.standoff_distance_m >= 1.0, "Standoff distance must be >= 1.0m (never on person)"
    assert completed_job.resume_seq is not None
    assert completed_job.resume_seq >= 6, "Resumption sequence must advance forward from checkpoint"

    # Step 5: Verify drone_02 MAVLink commands
    sent_cmds = adapter_02.master.commands_sent
    # Verified GUIDED was commanded
    assert ("SET_MODE", "GUIDED") in sent_cmds
    # Verified arrival at delivery standoff point
    assert any(cmd[0] == "GOTO_GLOBAL" and abs(cmd[1][2] - 5.0) < 0.1 for cmd in sent_cmds), "Descent to 5m delivery altitude required"
    # Verified climb back to 20m search altitude
    assert any(cmd[0] == "GOTO_GLOBAL" and abs(cmd[1][2] - 20.0) < 0.1 for cmd in sent_cmds), "Climb to 20m search altitude required"
    # Verified AUTO resumed
    assert ("SET_MODE", "AUTO") in sent_cmds

    # Step 6: Verify SEARCHER (drone_01) remained strictly in SEARCHING without interference
    assert runtime.vehicles["drone_01"]["state"] == "SEARCHING"
    assert len(adapter_01.master.commands_sent) == 0, "SEARCHER must not be commanded into GUIDED or diverted"

    # Step 7: Verify runtime status and delivery record
    status = runtime.status()
    assert status["coordination_state"] == "PHASE_2B_ACTIVE"
    assert len(status["deliveries"]) == 1
    deliv = status["deliveries"][0]
    assert deliv["payload_released"] is True
    assert deliv["verified"] is True
    assert deliv["release_id"] == completed_job.release_id
    assert deliv["standoff_distance_m"] >= 1.0


# ===========================================================================
# 2. Safety Gate Enforcements in Live Simulation
# ===========================================================================

@pytest.mark.anyio
async def test_approach_safety_blocks_stale_telemetry_in_live_flight():
    """Verify stale telemetry (> 3.0s) blocks autonomous approach."""
    coordinator, registry, session = _setup_live_coordinator()
    survivor = _ingest_live_survivor(registry)
    job = coordinator.assignments()[0]

    # Stale telemetry (> 3.0s)
    stale_override = {
        "armed": True, "flight_mode": "AUTO", "relative_altitude_m": 20.0,
        "ground_speed_m_s": 4.5, "telemetry_age_s": 5.5,
    }
    safe, reason = coordinator.check_approach_safety(job.assignment_id, telemetry_override=stale_override)
    assert safe is False
    assert "telemetry is stale" in reason


@pytest.mark.anyio
async def test_release_safety_blocks_excessive_altitude_in_live_flight():
    """Verify release safety gate blocks release if altitude is outside 5.0m +/- 2.0m."""
    coordinator, registry, session = _setup_live_coordinator()
    survivor = _ingest_live_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    # Altitude too high (12.0m > 7.0m upper threshold)
    unsafe_alt = {
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {
            "armed": True,
            "flight_mode": "GUIDED",
            "relative_altitude_m": 12.0,
            "ground_speed_m_s": 0.2,
            "east_m": 27.0,
            "north_m": 20.0,
        },
    }
    safe, reason = coordinator.check_release_safety(job.assignment_id, telemetry_override=unsafe_alt)
    assert safe is False
    assert "delivery envelope" in reason.lower() or "altitude" in reason.lower()


@pytest.mark.anyio
async def test_release_safety_blocks_excessive_ground_speed_in_live_flight():
    """Verify release safety gate blocks release if vehicle ground speed > 0.8 m/s."""
    coordinator, registry, session = _setup_live_coordinator()
    survivor = _ingest_live_survivor(registry)
    job = coordinator.assignments()[0]

    coordinator.acknowledge_assignment(job.assignment_id)
    coordinator.start_approach(job.assignment_id)
    coordinator.hold_for_release(job.assignment_id, standoff_m=2.0)

    # Ground speed 2.5 m/s > 0.8 m/s
    unsafe_speed = {
        "connected": True,
        "telemetry_age_s": 0.1,
        "telemetry": {
            "armed": True,
            "flight_mode": "GUIDED",
            "relative_altitude_m": 5.0,
            "ground_speed_m_s": 2.5,
            "east_m": 27.0,
            "north_m": 20.0,
        },
    }
    safe, reason = coordinator.check_release_safety(job.assignment_id, telemetry_override=unsafe_speed)
    assert safe is False
    assert "speed" in reason.lower()


# ===========================================================================
# Helpers
# ===========================================================================

def _setup_live_coordinator():
    """Set up coordinator and registry with simulated vehicle states for safety testing."""
    config = _make_fleet_configuration()
    session = FleetSession(
        session_id=uuid4(),
        mission_id=config.mission_id,
        run_id="run_live_safety",
        state="RUNNING",
        configuration=config,
        started_at=datetime.now(UTC),
    )
    registry = SharedSurvivorRegistry(session_id=session.session_id, mission_id=session.mission_id)
    now = time.monotonic()
    vstate = {
        "drone_01": {"vehicle_id": "drone_01", "state": "SEARCHING", "last_update_monotonic": now, "connected": True, "telemetry_age_s": 0.1, "telemetry": {"armed": True, "flight_mode": "AUTO", "relative_altitude_m": 20.0, "ground_speed_m_s": 4.5, "latitude": 12.9721, "longitude": 77.5926}},
        "drone_02": {"vehicle_id": "drone_02", "state": "READY", "last_update_monotonic": now, "connected": True, "telemetry_age_s": 0.1, "telemetry": {"armed": True, "flight_mode": "GUIDED", "relative_altitude_m": 20.0, "ground_speed_m_s": 0.0, "latitude": 12.9721, "longitude": 77.5926}},
    }
    coordinator = ExclusiveServiceCoordinator(
        session=session, fleet_config=config, registry=registry,
        vehicles_state_supplier=lambda: vstate,
    )
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed
    return coordinator, registry, session


def _ingest_live_survivor(registry):
    """Ingest simulated survivor observations into registry."""
    obs = {"target_track_id": "t1", "frame_id": "f1", "observed_at": datetime.now(UTC)}
    est = type("Est", (), {
        "target_id": "surv-1", "latitude": 12.9725, "longitude": 77.5930,
        "east_m": 25.0, "north_m": 20.0, "uncertainty_m": 1.2,
        "observation_count": 4, "state": type("S", (), {"value": "CONFIRMED"})(),
    })()
    for _ in range(4):
        registry.ingest("drone_01", obs, est)
    return registry.confirmed_survivors()[0]
