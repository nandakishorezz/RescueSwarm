"""Phase 2A.1 Live Survivor-to-Assignment Integration Acceptance Test Suite.

Validates the complete data flow:
Real Camera Frames -> YOLO Inference (best.pt) -> Temporal Confirmation ->
Shared Canonical Survivor Registry -> Exclusive Service Coordinator -> Multi-Drone UI Status.

Covers:
1. Real YOLO inference on genuine simulation camera frames with best.pt checkpoint.
2. Camera attribution, port isolation, and vehicle ID binding.
3. Candidate progression: raw detections remain unconfirmed candidates until temporal criteria met.
4. Promotion to canonical survivor (survivor-001) in SharedSurvivorRegistry with geolocation/uncertainty.
5. Coordinator exclusive allocation: assigned to eligible SERVICER (drone_02), never SEARCHER (drone_01).
6. Idempotency: repeated observations update existing record without creating duplicate survivors or jobs.
7. Cross-drone observation association into the shared canonical record.
8. Role enforcement: SEARCHER strictly forbidden from service jobs (PermissionError).
9. Stale/disconnected telemetry ineligibility and queuing with clear reason.
10. Automatic queued job reallocation upon telemetry recovery.
11. Authoritative MultiDroneRuntime status serialization for frontend UI.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import time
from uuid import UUID, uuid4

import cv2
import numpy as np
import pytest
from ultralytics import YOLO

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.domain.vehicle import VehicleState
from ground.backend.app.multidrone.contracts import (
    AssignmentState,
    DroneInstance,
    DroneRole,
    FleetConfiguration,
    FleetSession,
    default_configuration,
)
from ground.backend.app.multidrone.state_machine import DroneLifecycleState
from ground.backend.app.multidrone.coordinator import ExclusiveServiceCoordinator
from ground.backend.app.multidrone.registry import SharedSurvivorRegistry
from ground.backend.app.sitl.live_survivor_geolocation import LiveSurvivorGeolocator
from ground.backend.app.sitl.surveillance_service import (
    ACTIVE_DETECTION_CONFIDENCE,
    ACTIVE_INFERENCE_SIZE,
    SurveillanceService,
    accepts_active_detection,
    detection_display_label,
)
from rescue_core.simulation.profiles import load_camera_profile


ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT_PATH = ROOT / "data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt"
VAL_IMAGES_DIR = ROOT / "data/training/stage13sim_nadir_person_v3/images/val"
VAL_LABELS_DIR = ROOT / "data/training/stage13sim_nadir_person_v3/labels/val"
HOLDOUT_DIR = ROOT / "data/training/stage13sim_nadir_person_v3/mission_holdout/images"
ORIGIN = GeoPoint(latitude=12.9720, longitude=77.5925, altitude_m=0.0)


def _load_camera():
    return load_camera_profile(ROOT / "simulation/profiles/ov9281_reference_simulation.yaml")


def _make_vehicle_state(vehicle_id="drone_01", altitude_m=20.0, lat=12.9720, lon=77.5925):
    return VehicleState(
        vehicle_id=vehicle_id,
        name=vehicle_id,
        connected=True,
        latitude=lat,
        longitude=lon,
        relative_altitude_m=altitude_m,
        absolute_altitude_m=altitude_m,
        gps_fix_type=3,
        satellites=12,
        quaternion_body_to_ned=(1.0, 0.0, 0.0, 0.0),
        simulated=True,
    )


# ===========================================================================
# STAGE 1: Real YOLO Inference on Genuine Simulation Camera Frames
# ===========================================================================

def test_stage1_real_yolo_inference_on_simulation_frames():
    """Verify that the real best.pt checkpoint detects the simulated person in real camera frames."""
    assert CHECKPOINT_PATH.exists(), f"Checkpoint {CHECKPOINT_PATH} must exist"
    model = YOLO(str(CHECKPOINT_PATH))

    # Find positive frames from validation dataset
    pos_labels = [p.stem for p in VAL_LABELS_DIR.glob("*.txt") if p.read_text().strip()]
    assert len(pos_labels) > 0, "Validation set must have annotated positive frames"

    # Test real positive image
    sample_stem = pos_labels[0]
    img_path = VAL_IMAGES_DIR / f"{sample_stem}.png"
    if not img_path.exists():
        img_path = VAL_IMAGES_DIR / f"{sample_stem}.jpg"
    assert img_path.exists(), f"Image {img_path} must exist"

    img = cv2.imread(str(img_path))
    assert img is not None
    height, width = img.shape[:2]

    results = model(img, conf=ACTIVE_DETECTION_CONFIDENCE, imgsz=ACTIVE_INFERENCE_SIZE, verbose=False)[0]
    assert len(results.boxes) >= 1, "YOLO must detect at least one person in the known positive frame"

    box = results.boxes[0]
    class_id = int(box.cls[0])
    conf = float(box.conf[0])
    assert class_id == 0, "Target detection must be class 0"
    assert conf >= ACTIVE_DETECTION_CONFIDENCE, f"Confidence {conf} must exceed {ACTIVE_DETECTION_CONFIDENCE}"
    label = detection_display_label(CHECKPOINT_PATH, class_id, results.names.get(class_id, "item"))
    assert label == "person", "Class 0 must be labeled 'person'"

    # Test negative holdout images produce zero false positives
    holdout_imgs = list(HOLDOUT_DIR.glob("*.png"))[:3]
    for h_img_p in holdout_imgs:
        h_img = cv2.imread(str(h_img_p))
        if h_img is not None:
            h_res = model(h_img, conf=ACTIVE_DETECTION_CONFIDENCE, imgsz=ACTIVE_INFERENCE_SIZE, verbose=False)[0]
            assert len(h_res.boxes) == 0, "Negative holdout frame must produce 0 false detections"


# ===========================================================================
# STAGE 2: Camera Attribution and Vehicle ID Binding
# ===========================================================================

def test_stage2_camera_attribution_and_port_isolation(tmp_path):
    """Verify camera frame IDs, ports, and geolocator vehicle attribution are properly isolated."""
    cam1 = SurveillanceService(tmp_path, None, port=45678, vehicle_id="drone_01", precise_helper_stop=True)
    cam2 = SurveillanceService(tmp_path, None, port=45679, vehicle_id="drone_02", precise_helper_stop=True)

    cam1.frame_count = 5
    cam2.frame_count = 5

    assert cam1._frame_id() == "drone_01:camera:5"
    assert cam2._frame_id() == "drone_02:camera:5"
    assert cam1.port == 45678
    assert cam2.port == 45679

    # Check vehicle_id in LiveSurvivorGeolocator
    cam_profile = _load_camera()
    locator1 = LiveSurvivorGeolocator("m1", ORIGIN, cam_profile, vehicle_id="drone_01")
    locator2 = LiveSurvivorGeolocator("m1", ORIGIN, cam_profile, vehicle_id="drone_02")
    assert locator1.vehicle_id == "drone_01"
    assert locator2.vehicle_id == "drone_02"


# ===========================================================================
# STAGES 3 - 7: Full Pipeline: Real Frame -> Inference -> Temporal Confirmation
# -> Shared Canonical Registry -> Exclusive Coordinator Assignment
# ===========================================================================

def test_stage3_to_7_full_pipeline_acceptance():
    """Verify steps 1 through 7 of the Phase 2A.1 acceptance specification.

    1. Vehicles connect and report telemetry.
    2. Camera stream is attributed to drone ID.
    3. YOLO processes real frames.
    4. Candidate remains unconfirmed until temporal criteria met.
    5. Confirmed survivor enters shared registry as 'survivor-001'.
    6. Coordinator creates exactly 1 active service assignment.
    7. Allocated to SERVICER (drone_02), never SEARCHER (drone_01).
    """
    session_id = uuid4()
    mission_id = "mission_acceptance"
    fleet_config = default_configuration(mission_id)

    # drone_01 is SEARCHER, drone_02 is SERVICER
    drones = {d.vehicle_id: d for d in fleet_config.drones}
    assert drones["drone_01"].role == DroneRole.SEARCHER
    assert drones["drone_02"].role == DroneRole.SERVICER

    # Initialize Registry and Coordinator
    events_log = []
    def record_event(vid, ev_type, msg, sev="INFO", meta=None):
        events_log.append({"vid": vid, "type": ev_type, "msg": msg, "sev": sev, "meta": meta})

    vehicles_state = {
        "drone_01": {
            "vehicle_id": "drone_01",
            "state": DroneLifecycleState.SEARCHING.value,
            "connected": True,
            "telemetry_age_s": 0.1,
            "last_update_monotonic": time.monotonic(),
            "telemetry": {"armed": True, "mode": "AUTO"},
        },
        "drone_02": {
            "vehicle_id": "drone_02",
            "state": DroneLifecycleState.READY.value,
            "connected": True,
            "telemetry_age_s": 0.1,
            "last_update_monotonic": time.monotonic(),
            "telemetry": {"armed": True, "mode": "GUIDED"},
        },
    }

    session = FleetSession(
        session_id=session_id,
        mission_id=mission_id,
        run_id="run_acceptance",
        state="RUNNING",
        configuration=fleet_config,
        started_at=datetime.now(UTC),
    )

    registry = SharedSurvivorRegistry(
        session_id=session_id,
        mission_id=mission_id,
        minimum_observations=3,
        minimum_time_span_s=0.15,
        on_event_callback=record_event,
    )

    coordinator = ExclusiveServiceCoordinator(
        session=session,
        fleet_config=fleet_config,
        registry=registry,
        vehicles_state_supplier=lambda: vehicles_state,
        event_callback=record_event,
    )
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed

    # Set up geolocator for drone_01
    cam_profile = _load_camera()
    locator = LiveSurvivorGeolocator(mission_id, ORIGIN, cam_profile, vehicle_id="drone_01")

    # Run real YOLO inference on real simulation frame
    model = YOLO(str(CHECKPOINT_PATH))
    pos_labels = [p.stem for p in VAL_LABELS_DIR.glob("*.txt") if p.read_text().strip()]
    sample_stem = pos_labels[0]
    img_path = VAL_IMAGES_DIR / f"{sample_stem}.png"
    if not img_path.exists():
        img_path = VAL_IMAGES_DIR / f"{sample_stem}.jpg"
    img = cv2.imread(str(img_path))
    h, w = img.shape[:2]

    yolo_res = model(img, conf=ACTIVE_DETECTION_CONFIDENCE, imgsz=ACTIVE_INFERENCE_SIZE, verbose=False)[0]
    assert len(yolo_res.boxes) >= 1
    det_box = yolo_res.boxes[0]
    x1, y1, x2, y2 = [int(v) for v in det_box.xyxy[0]]
    conf = float(det_box.conf[0])
    raw_detection = {
        "bbox": [x1, y1, x2 - x1, y2 - y1],
        "conf": conf,
        "class": "item",
        "class_id": 0,
        "display_label": "person",
    }

    # Step 1: Feed Frame 1 (t = 0.0s)
    base_ns = time.monotonic_ns()
    vehicle = _make_vehicle_state("drone_01", altitude_m=20.0)
    locator.update_vehicle_pose(vehicle, monotonic_time_ns=base_ns)

    outcomes_1 = locator.observe(
        frame_id="drone_01:camera:1",
        timestamp_monotonic_ns=base_ns,
        detections=[raw_detection],
        width=w, height=h,
    )
    assert len(outcomes_1) == 1
    assert outcomes_1[0].observation is not None
    assert outcomes_1[0].estimate.state.value == "CANDIDATE"

    # Ingest into registry
    res_1 = registry.ingest("drone_01", outcomes_1[0].observation, outcomes_1[0].estimate)
    # Step 4 check: MUST remain unconfirmed
    assert res_1 is None, "Frame 1 must remain an unconfirmed candidate"
    assert len(registry.confirmed_survivors()) == 0
    assert len(coordinator.assignments()) == 0

    # Step 2: Feed Frame 2 (t = 0.08s < 0.15s threshold)
    t2_ns = base_ns + 80_000_000
    locator.update_vehicle_pose(vehicle, monotonic_time_ns=t2_ns)
    outcomes_2 = locator.observe(
        frame_id="drone_01:camera:2",
        timestamp_monotonic_ns=t2_ns,
        detections=[raw_detection],
        width=w, height=h,
    )
    res_2 = registry.ingest("drone_01", outcomes_2[0].observation, outcomes_2[0].estimate)
    assert res_2 is None, "Frame 2 must still remain an unconfirmed candidate"
    assert len(registry.confirmed_survivors()) == 0
    assert len(coordinator.assignments()) == 0

    # Step 3: Feed Frame 3 (t = 0.20s > 0.15s threshold, 3 distinct frames)
    t3_ns = base_ns + 200_000_000
    locator.update_vehicle_pose(vehicle, monotonic_time_ns=t3_ns)
    outcomes_3 = locator.observe(
        frame_id="drone_01:camera:3",
        timestamp_monotonic_ns=t3_ns,
        detections=[raw_detection],
        width=w, height=h,
    )
    assert outcomes_3[0].estimate.state.value == "CONFIRMED"

    res_3 = registry.ingest("drone_01", outcomes_3[0].observation, outcomes_3[0].estimate)

    # Step 5 check: Confirmed survivor record
    assert res_3 is not None, "Frame 3 must promote candidate to confirmed survivor"
    assert res_3.canonical_id == "survivor-001"
    assert res_3.confirmation_status == "CONFIRMED"
    assert res_3.observation_count >= 3
    assert res_3.source_drone_ids == ["drone_01"]
    assert res_3.latitude is not None and res_3.longitude is not None
    assert res_3.uncertainty_m > 0
    assert res_3.confidence >= ACTIVE_DETECTION_CONFIDENCE

    # Step 6 & 7 check: Exclusive assignment to SERVICER (drone_02), NEVER SEARCHER (drone_01)
    assignments = coordinator.assignments()
    assert len(assignments) == 1, "Exactly one service assignment must be created"
    job = assignments[0]
    assert job.canonical_survivor_id == "survivor-001"
    assert job.servicer_id == "drone_02", "Job must be allocated to SERVICER drone_02, NOT SEARCHER drone_01"
    assert job.state == AssignmentState.ASSIGNED

    # Verify survivor in registry reflects the assignment
    updated_survivor = registry.get_survivor("survivor-001")
    assert updated_survivor.assignment_id == str(job.assignment_id)
    assert updated_survivor.assigned_servicer_id == "drone_02"
    assert updated_survivor.assignment_state == AssignmentState.ASSIGNED


# ===========================================================================
# STAGES 8 - 10: Repeated Observations, Cross-Drone Association, Lifecycle
# ===========================================================================

def test_stage8_to_10_repeated_observations_and_cross_drone_association():
    """Verify steps 8 through 10 of the acceptance specification.

    8. UI status updates with authoritative backend state.
    9. Repeated observations update existing record without duplicates.
    10. Cross-drone observation maps to existing survivor-001.
    """
    session_id = uuid4()
    mission_id = "mission_acceptance_repeat"
    fleet_config = default_configuration(mission_id)

    vehicles_state = {
        "drone_01": {"vehicle_id": "drone_01", "state": "SEARCHING", "connected": True, "telemetry_age_s": 0.1},
        "drone_02": {"vehicle_id": "drone_02", "state": "READY", "connected": True, "telemetry_age_s": 0.1},
    }

    session = FleetSession(
        session_id=session_id, mission_id=mission_id, run_id="r1",
        state="RUNNING", configuration=fleet_config, started_at=datetime.now(UTC),
    )

    registry = SharedSurvivorRegistry(session_id=session_id, mission_id=mission_id)
    coordinator = ExclusiveServiceCoordinator(
        session=session, fleet_config=fleet_config, registry=registry,
        vehicles_state_supplier=lambda: vehicles_state,
    )
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed

    # Set up geolocator for drone_01
    cam_profile = _load_camera()
    locator1 = LiveSurvivorGeolocator(mission_id, ORIGIN, cam_profile, vehicle_id="drone_01")
    vehicle1 = _make_vehicle_state("drone_01", altitude_m=20.0)

    # Use detection box
    det = {"bbox": [836, 588, 62, 162], "conf": 0.89, "class": "item", "class_id": 0}

    # Ingest 3 frames to confirm
    base_ns = time.monotonic_ns()
    for i in range(3):
        ts = base_ns + i * 100_000_000
        locator1.update_vehicle_pose(vehicle1, monotonic_time_ns=ts)
        out = locator1.observe(frame_id=f"drone_01:camera:{i+1}", timestamp_monotonic_ns=ts, detections=[det], width=1280, height=800)[0]
        registry.ingest("drone_01", out.observation, out.estimate)

    assert len(registry.confirmed_survivors()) == 1
    assert len(coordinator.assignments()) == 1
    initial_count = registry.get_survivor("survivor-001").observation_count
    assert initial_count >= 3

    # Step 9: Repeated observation from drone_01 (frame 4)
    ts_4 = base_ns + 400_000_000
    locator1.update_vehicle_pose(vehicle1, monotonic_time_ns=ts_4)
    out_4 = locator1.observe(frame_id="drone_01:camera:4", timestamp_monotonic_ns=ts_4, detections=[det], width=1280, height=800)[0]
    res_4 = registry.ingest("drone_01", out_4.observation, out_4.estimate)

    assert res_4.canonical_id == "survivor-001", "Repeated observation must update existing survivor-001"
    assert len(registry.confirmed_survivors()) == 1, "Must NOT create duplicate survivor"
    assert len(coordinator.assignments()) == 1, "Must NOT create duplicate assignment"
    assert res_4.observation_count == initial_count + 1

    # Step 10: Cross-drone observation from drone_02 observing the same location
    locator2 = LiveSurvivorGeolocator(mission_id, ORIGIN, cam_profile, vehicle_id="drone_02")
    vehicle2 = _make_vehicle_state("drone_02", altitude_m=20.0)
    ts_5 = base_ns + 500_000_000
    locator2.update_vehicle_pose(vehicle2, monotonic_time_ns=ts_5)
    out_5 = locator2.observe(frame_id="drone_02:camera:1", timestamp_monotonic_ns=ts_5, detections=[det], width=1280, height=800)[0]

    res_5 = registry.ingest("drone_02", out_5.observation, out_5.estimate)
    assert res_5.canonical_id == "survivor-001", "Cross-drone observation must associate into survivor-001"
    assert set(res_5.source_drone_ids) == {"drone_01", "drone_02"}, "Both drone IDs must be in source_drone_ids"
    assert len(registry.confirmed_survivors()) == 1
    assert len(coordinator.assignments()) == 1


# ===========================================================================
# NEGATIVE AND ELIGIBILITY CASES
# ===========================================================================

def test_negative_searcher_forbidden_from_service_assignment():
    """Verify that a SEARCHER can NEVER be assigned a service job."""
    session_id = uuid4()
    mission_id = "mission_negative_roles"
    fleet_config = default_configuration(mission_id)

    registry = SharedSurvivorRegistry(session_id=session_id, mission_id=mission_id)
    coordinator = ExclusiveServiceCoordinator(
        session=FleetSession(
            session_id=session_id, mission_id=mission_id, run_id="r", state="RUNNING",
            configuration=fleet_config, started_at=datetime.now(UTC),
        ),
        fleet_config=fleet_config,
        registry=registry,
    )

    # Ingest confirmed survivor
    obs = {"target_latitude": 12.972, "target_longitude": 77.592, "target_east_m": 0.0, "target_north_m": 0.0, "confidence": 0.9}
    est = type("Est", (), {"state": type("St", (), {"value": "CONFIRMED"}), "east_m": 0.0, "north_m": 0.0})()
    survivor = registry.ingest("drone_01", obs, est)
    assert survivor is not None

    # Explicitly attempt to force assignment to SEARCHER drone_01
    with pytest.raises(PermissionError, match="SEARCHER"):
        coordinator.allocate_job(survivor.canonical_id, force_servicer_id="drone_01")


def test_negative_unconfirmed_candidate_cannot_create_assignment():
    """Verify that an unconfirmed survivor cannot create a service assignment."""
    session_id = uuid4()
    mission_id = "mission_negative_candidate"
    fleet_config = default_configuration(mission_id)

    registry = SharedSurvivorRegistry(session_id=session_id, mission_id=mission_id)
    coordinator = ExclusiveServiceCoordinator(
        session=FleetSession(
            session_id=session_id, mission_id=mission_id, run_id="r", state="RUNNING",
            configuration=fleet_config, started_at=datetime.now(UTC),
        ),
        fleet_config=fleet_config,
        registry=registry,
    )

    # Ingest 1 candidate observation
    obs = {"target_latitude": 12.972, "target_longitude": 77.592, "target_east_m": 0.0, "target_north_m": 0.0, "confidence": 0.8}
    est = type("Est", (), {"state": type("St", (), {"value": "CANDIDATE"}), "east_m": 0.0, "north_m": 0.0})()
    result = registry.ingest("drone_01", obs, est)
    assert result is None
    assert len(registry.confirmed_survivors()) == 0

    # Attempt to allocate job for non-existent / candidate
    with pytest.raises(ValueError, match="not confirmed"):
        coordinator.allocate_job("candidate-1")


def test_negative_stale_telemetry_marks_servicer_ineligible_and_queues_with_reason():
    """Verify that stale telemetry (>3s) makes SERVICER ineligible, queuing the job with clear reason."""
    session_id = uuid4()
    mission_id = "mission_negative_stale"
    fleet_config = default_configuration(mission_id)

    # drone_02 has STALE telemetry (age 6.5s)
    vehicles_state = {
        "drone_01": {"vehicle_id": "drone_01", "state": "SEARCHING", "connected": True, "telemetry_age_s": 0.1},
        "drone_02": {"vehicle_id": "drone_02", "state": "READY", "connected": False, "telemetry_age_s": 6.5},
    }

    session = FleetSession(
        session_id=session_id, mission_id=mission_id, run_id="r", state="RUNNING",
        configuration=fleet_config, started_at=datetime.now(UTC),
    )
    registry = SharedSurvivorRegistry(session_id=session_id, mission_id=mission_id)
    coordinator = ExclusiveServiceCoordinator(
        session=session, fleet_config=fleet_config, registry=registry,
        vehicles_state_supplier=lambda: vehicles_state,
    )
    registry.on_confirmed_callback = coordinator.on_survivor_confirmed

    # Ingest confirmed survivor
    obs = {"target_latitude": 12.972, "target_longitude": 77.592, "target_east_m": 0.0, "target_north_m": 0.0, "confidence": 0.9}
    est = type("Est", (), {"state": type("St", (), {"value": "CONFIRMED"}), "east_m": 0.0, "north_m": 0.0})()
    survivor = registry.ingest("drone_01", obs, est)

    assignments = coordinator.assignments()
    assert len(assignments) == 1
    job = assignments[0]
    assert job.state == AssignmentState.QUEUED
    assert job.servicer_id is None
    assert "telemetry is stale" in job.reason.lower() or "disconnected" in job.reason.lower()

    # Now simulate telemetry recovery on drone_02
    vehicles_state["drone_02"]["connected"] = True
    vehicles_state["drone_02"]["telemetry_age_s"] = 0.2
    vehicles_state["drone_02"]["last_update_monotonic"] = time.monotonic()

    coordinator.on_telemetry_or_state_change()

    # Verify job is automatically reallocated to recovered servicer
    updated_job = coordinator.get_assignment(job.assignment_id)
    assert updated_job.state == AssignmentState.ASSIGNED
    assert updated_job.servicer_id == "drone_02"


def test_multidrone_runtime_status_and_frontend_contract():
    """Verify runtime.status() serialization contains authoritative Phase 2A state for the UI."""
    from ground.backend.app.multidrone.runtime import MultiDroneRuntime
    from ground.backend.app.multidrone.state_machine import FleetState, FleetStateMachine

    runtime = MultiDroneRuntime.__new__(MultiDroneRuntime)
    runtime.state_machine = FleetStateMachine(FleetState.RUNNING)
    runtime.drone_state_machines = {}
    runtime.vehicles = {
        "drone_01": {"vehicle_id": "drone_01", "state": "SEARCHING", "last_update_monotonic": time.monotonic()},
        "drone_02": {"vehicle_id": "drone_02", "state": "READY", "last_update_monotonic": time.monotonic()},
    }
    runtime.cameras = {}
    runtime.camera_history = {}
    runtime.camera_started_at = {}
    runtime.failed_vehicles = []
    runtime.trails = {"drone_01": [], "drone_02": []}
    runtime.events = []
    runtime.observations = []
    runtime.error = None
    runtime.plan = None
    runtime.stop_requested = False

    session_id = uuid4()
    mission_id = "mission_runtime_status"
    fleet_config = default_configuration(mission_id)
    runtime.session = FleetSession(
        session_id=session_id, mission_id=mission_id, run_id="r_status",
        state="RUNNING", configuration=fleet_config, started_at=datetime.now(UTC),
    )

    runtime.registry = SharedSurvivorRegistry(session_id=session_id, mission_id=mission_id)
    runtime.coordinator = ExclusiveServiceCoordinator(
        session=runtime.session, fleet_config=fleet_config, registry=runtime.registry,
        vehicles_state_supplier=lambda: runtime._get_vehicles_snapshot(),
    )
    runtime.registry.on_confirmed_callback = runtime.coordinator.on_survivor_confirmed

    # Ingest a confirmed survivor
    obs = {"target_latitude": 12.972, "target_longitude": 77.592, "target_east_m": 0.0, "target_north_m": 0.0, "confidence": 0.9}
    est = type("Est", (), {"state": type("St", (), {"value": "CONFIRMED"}), "east_m": 0.0, "north_m": 0.0})()
    runtime.registry.ingest("drone_01", obs, est)

    # Check status payload
    status = runtime.status()
    assert status["coordination_state"] in {"PHASE_2A_ACTIVE", "PHASE_2B_ACTIVE"}
    assert len(status["targets"]) == 1
    target = status["targets"][0]
    assert target["canonical_id"] == "survivor-001"
    assert target["assigned_servicer_id"] == "drone_02"
    assert target["assignment_state"] == "ASSIGNED"

    assert len(status["assignments"]) == 1
    assignment = status["assignments"][0]
    assert assignment["canonical_survivor_id"] == "survivor-001"
    assert assignment["servicer_id"] == "drone_02"
    assert assignment["state"] == "ASSIGNED"

    # Role protection check on runtime.authorize_service
    with pytest.raises(PermissionError, match="SEARCHER"):
        runtime.authorize_service("drone_01")

    # Authorizing eligible SERVICER succeeds
    runtime.authorize_service("drone_02")

