from __future__ import annotations

import ast
import hashlib
import io
import json
import struct
import threading
from types import SimpleNamespace
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ground.backend.app.config import load_ground_config
from ground.backend.app.main import create_app
from rescue_core.simulation.evaluator import (
    PerceptionEvaluator,
    WorldTruthImageBBox,
    WorldTruthSegmentationFrame,
    compact_visibility,
    semantic_mask_boxes,
)
from rescue_core.simulation.perception.camera_source import GazeboCameraSource, _mono8, _native_pixels
from rescue_core.simulation.profiles import load_camera_profile, load_drone_profile
from rescue_core.simulation.orchestration import SimulationManager
from rescue_core.simulation.world import GazeboVehicleModelBuilder
from rescue_core.vision.models import Detection
from scripts.run_stage13sim_phase2 import build_definition, build_mission


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.stage13sim
def test_rgb_virtual_sensor_becomes_deterministic_mono8_without_rescale():
    rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
    mono = _mono8(rgb, 3, 1, 9, GazeboCameraSource.RGB_INT8)
    assert mono.shape == (1, 3)
    assert mono.dtype == np.uint8
    assert mono.tolist() == [[76, 149, 28]]


@pytest.mark.stage13sim
def test_semantic_labels_are_not_destroyed_by_mono_conversion():
    rgb = np.array([[[0, 0, 0], [101, 101, 101]], [[0, 0, 0], [101, 101, 101]]], dtype=np.uint8)
    decoded, name = _native_pixels(rgb.tobytes(), 2, 2, 6, GazeboCameraSource.RGB_INT8)
    from rescue_core.simulation.evaluator.perception import _decode_label_image
    labels = _decode_label_image(decoded, name)
    frame = WorldTruthSegmentationFrame("truth:1", 1, 2, 2, labels, "checksum")
    boxes = semantic_mask_boxes(frame, minimum_pixels=1)
    assert [(box.evaluator_label, box.visible_pixels) for box in boxes] == [(101, 2)]
    compact = compact_visibility(frame, minimum_pixels=1)
    assert compact.boxes == tuple(boxes)
    assert not hasattr(compact, "label_image")


@pytest.mark.stage13sim
def test_evaluator_matches_after_inference_without_suppressing_false_positives():
    truth = [WorldTruthImageBBox(101, 10, 10, 30, 50, 100, 100, 800)]
    detections = [
        Detection(0, "person", .8, 10, 10, 30, 50, 100, 100),
        Detection(0, "person", .4, 70, 70, 90, 95, 100, 100),
    ]
    result = PerceptionEvaluator(.5).evaluate(detections, truth)
    assert result.true_positives == 1
    assert result.false_positive_indices == [1]
    assert result.false_negative_indices == []


@pytest.mark.stage13sim
def test_autonomy_and_evaluator_cameras_share_one_profile_and_matched_optics():
    camera = load_camera_profile(ROOT / "simulation/profiles/ov9281_phase2_oblique_simulation.yaml")
    drone = load_drone_profile(ROOT / "simulation/profiles/rescueswarm_reference_quad.yaml")
    root = ET.fromstring(GazeboVehicleModelBuilder().build(drone, camera))
    normal = root.find(".//sensor[@type='camera']")
    truth = root.find(".//sensor[@type='segmentation_camera']")
    assert normal is not None and truth is not None
    for suffix in ("camera/horizontal_fov", "camera/image/width", "camera/image/height", "update_rate", "camera/clip/near", "camera/clip/far"):
        assert normal.findtext(suffix) == truth.findtext(suffix)
    assert normal.findtext("camera/image/format") == "L_INT8"


@pytest.mark.stage13sim
def test_hidden_survivor_shift_does_not_change_pre_detection_mission_fingerprint():
    first = build_definition("first")
    second = build_definition("second")
    moved = second.survivors.survivors[0].model_copy(update={"latitude": second.survivors.survivors[0].latitude + .00001})
    second.survivors = second.survivors.model_copy(update={"survivors": [moved, *second.survivors.survivors[1:]]})
    first_mission, _ = build_mission(first, 8)
    second_mission, _ = build_mission(second, 8)
    assert first_mission.fingerprint == second_mission.fingerprint


@pytest.mark.stage13sim
def test_frozen_autonomy_has_no_truth_or_evaluator_imports():
    roots = [
        ROOT / "rescue_core/vision",
        ROOT / "rescue_core/simulation/perception",
        ROOT / "rescue_core/geolocation",
        ROOT / "rescue_core/autonomy",
        ROOT / "ground/backend/app/planning",
        ROOT / "ground/backend/app/fleet",
        ROOT / "ground/backend/app/services",
        ROOT / "ground/backend/app/repositories",
        ROOT / "ground/backend/app/domain",
        ROOT / "ground/backend/app/sitl",
    ]
    forbidden = {"rescue_core.simulation.truth", "rescue_core.simulation.evaluator"}
    for package in roots:
        if not package.exists():
            continue
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = {
                node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            imports |= {
                alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names
            }
            assert not any(any(name == item or name.startswith(item + ".") for item in forbidden) for name in imports), path


@pytest.mark.stage13sim
def test_phase2_pipeline_source_has_no_truth_coordinate_shortcut():
    path = ROOT / "rescue_core/simulation/perception/pipeline.py"
    source = path.read_text(encoding="utf-8")
    for forbidden in ("survivor.position", "distance_to_survivor", "project_bbox"):
        assert forbidden not in source


@pytest.mark.stage13sim
def test_normal_perception_api_excludes_truth_and_observer_requires_opt_in(tmp_path):
    run = tmp_path / "phase2_api"
    (run / "perception_monitor").mkdir(parents=True)
    (run / "vision_status.json").write_text('{"frames_processed":12}', encoding="utf-8")
    (run / "phase2_run_summary.json").write_text(
        '{"raw_person_detections":3,"confirmed_targets":1,"truth_visible_person_frames":9}',
        encoding="utf-8",
    )
    (run / "PIXEL_ONLY_PERCEPTION_EVIDENCE.json").write_text(
        '{"SIMULATION_ONLY_GROUND_TRUTH_enu_m":[1,2],"proof_source":"CAMERA_PIXELS"}',
        encoding="utf-8",
    )
    config = load_ground_config(ROOT / "config/ground.yaml")
    config = config.model_copy(update={"database": config.database.model_copy(update={"url": f"sqlite:///{tmp_path / 'api.db'}"})})
    app = create_app(config)
    app.state.simulation_lab_manager = SimulationManager(tmp_path)
    with TestClient(app) as client:
        normal = client.get("/api/simulation/perception/latest")
        assert normal.status_code == 200
        assert "GROUND_TRUTH" not in normal.text and "truth_visible" not in normal.text
        assert client.get("/api/simulation/observer/perception/latest").status_code == 403
        observer = client.get("/api/simulation/observer/perception/latest?observer_ground_truth=true")
        assert observer.status_code == 200 and "SIMULATION_ONLY_GROUND_TRUTH" in observer.text


def bridge_stream(sequence=0, timestamp=0, **updates):
    pixels = bytes([10, 20, 30, 40])
    header = {
        "width": 2, "height": 2, "step": 2, "pixel_format_type": 1,
        "data_length": 4, "checksum_sha256": hashlib.sha256(pixels).hexdigest(),
        "source_sequence": sequence, "sim_capture_time_ns": timestamp,
        "bridge_receive_time_ns": 500, "frames_generated": sequence + 1,
        "frames_dropped_bridge": 0, "duplicate_frames": 0,
        **updates,
    }
    encoded = json.dumps(header).encode()
    return struct.pack("!I", len(encoded)) + encoded + pixels


def connected_camera(payload):
    source = GazeboCameraSource("/test", expected_width=2, expected_height=2)
    stream = io.BytesIO(payload)
    source._socket = SimpleNamespace(recv=stream.read)
    source._started = True
    return source


def test_transport_preserves_sensor_time_and_reports_unknown_capture_age():
    source = connected_camera(bridge_stream() + bridge_stream(3, 100) + bridge_stream(4, 200))
    first, second, third = source.read(), source.read(), source.read()
    assert first.camera_metadata.sensor_timestamp_ns == 0
    assert source.metrics.first_sim_time_ns == 0
    assert second.camera_metadata.sensor_timestamp_ns == 100
    assert second.dropped_since_previous == 2
    assert third.dropped_since_previous == 0
    assert first.sequence < second.sequence < third.sequence
    assert source.metrics.max_frame_age_ms is None
    assert second.camera_metadata.extra["capture_pose_alignment_verified"] is False
    assert second.capture_timestamp_sim_ns == 100
    assert second.capture_timestamp_monotonic_ns != second.capture_timestamp_sim_ns


@pytest.mark.parametrize("updates", [
    {"data_length": -1}, {"data_length": 2**40}, {"step": 1},
    {"pixel_format_type": 99}, {"width": 3}, {"height": 3},
])
def test_transport_rejects_invalid_schema_before_reading_pixels(updates):
    from rescue_core.vision.camera import CameraSourceError
    with pytest.raises(CameraSourceError, match="Invalid Gazebo image header"):
        connected_camera(bridge_stream(**updates)).read()


@pytest.mark.parametrize("sequence,timestamp", [(0, 200), (1, 100), (1, 50)])
def test_transport_rejects_duplicate_identity_timestamp_and_clock_reset(sequence, timestamp):
    from rescue_core.vision.camera import CameraSourceError
    source = connected_camera(bridge_stream(0, 100) + bridge_stream(sequence, timestamp))
    source.read()
    with pytest.raises(CameraSourceError):
        source.read()


def test_transport_disconnect_is_explicit():
    from rescue_core.vision.camera import CameraSourceError
    with pytest.raises(CameraSourceError, match="disconnected"):
        connected_camera(b"").read()


def test_camera_startup_timeout_cleans_up_without_deadlock(monkeypatch, tmp_path):
    from rescue_core.simulation.perception import camera_source as module
    from rescue_core.vision.camera import CameraSourceError
    process = SimpleNamespace(poll=lambda: None, wait=lambda timeout: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    clock = iter([0.0, 16.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    source = GazeboCameraSource("/missing", bridge_port=5555, bridge_script=tmp_path / "bridge.py")
    errors = []

    def start():
        try:
            source.start()
        except CameraSourceError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(1)
    assert not thread.is_alive()
    assert errors and "Timed out" in errors[0]
    assert source._process is None


def test_segmentation_keeps_dominant_person_and_excludes_other_semantics():
    labels = np.array([[101, 101], [101, 9]], dtype=np.uint32)
    frame = WorldTruthSegmentationFrame("truth", 1, 2, 2, labels, "hash")
    boxes = semantic_mask_boxes(frame, survivor_labels=frozenset({101}))
    assert len(boxes) == 1 and boxes[0].visible_pixels == 3
    assert boxes[0].edge_clipped


def test_semantic_decoder_rejects_color_instance_map():
    from rescue_core.simulation.evaluator.perception import _decode_label_image
    with pytest.raises(ValueError, match="semantic labels_map"):
        _decode_label_image(np.array([[[101, 0, 0]]], dtype=np.uint8), "RGB_INT8")


def test_evaluator_requires_person_class_and_deterministic_one_to_one_matching():
    truth = [WorldTruthImageBBox(101, 0, 0, 10, 10, 20, 20, 100)]
    person = Detection(0, "person", .8, 0, 0, 10, 10, 20, 20)
    car = Detection(2, "car", .9, 0, 0, 10, 10, 20, 20)
    result = PerceptionEvaluator(.5).evaluate([car, person, person], truth)
    assert result.matches[0].detection_index == 1
    assert result.false_positive_indices == [0, 2]


def test_target_evaluation_cannot_borrow_another_detection_in_same_frame(tmp_path):
    from scripts.run_stage13sim_phase2 import evaluate_after_inference
    from rescue_core.simulation.evaluator.perception import WorldTruthVisibilityFrame
    from rescue_core.vision.models import DetectionResult
    truth = {"survivors": [{
        "survivor_id": "hidden_01", "body_state": "standing",
        "position_enu_m": [0, 0, 0], "position_wgs84": [0, 0, 0],
    }]}
    (tmp_path / "SIMULATION_ONLY_GROUND_TRUTH_survivors.json").write_text(json.dumps(truth))
    result = DetectionResult(
        "frame", "source", 1, 1, 2, 3, "model", "onnx", "MONO8", "RGB", [
            Detection(0, "person", .8, 0, 0, 10, 10, 30, 30),
            Detection(0, "person", .8, 20, 20, 30, 30, 30, 30),
        ], 0, 1, 0, 2,
    )
    false_track = SimpleNamespace(target_id="false_track", inlier_observation_ids=["false_observation"])
    pipeline = SimpleNamespace(
        results=[{**result.as_dict(), "sim_capture_time_ns": 100}],
        observation_detections={"false_observation": ("frame", 1)},
        confirmed=lambda: [false_track],
    )
    mask = WorldTruthVisibilityFrame("truth", 100, (WorldTruthImageBBox(101, 0, 0, 10, 10, 30, 30, 100),))
    metrics = evaluate_after_inference(tmp_path, pipeline, [mask])
    assert metrics["confirmed_false_positive_targets"] == ["false_track"]
    assert metrics["measured_target_errors"] == 0
    assert len(result.detections) == 2


def test_phase2_cannot_claim_qualification_from_smoke_test():
    runner_source = (ROOT / "scripts/run_stage13sim_phase2.py").read_text(encoding="utf-8")
    pipeline_source = (ROOT / "rescue_core/simulation/perception/pipeline.py").read_text(encoding="utf-8")
    # Phase-2C: runner must remain fail-closed.
    assert 'result["passed"] = False' in runner_source
    # Phase-2C: direct clock blocker in runner (per-run check).
    assert '"DIRECT_SIM_CLOCK_NOT_VALID"' in runner_source
    # Phase-2C: remaining experiment qualifications still required.
    assert '"LIVE_FOV_OCCLUSION_AND_WIND_PIXEL_CONTROLS_NOT_QUALIFIED"' in runner_source
    assert '"ANGLE_ALTITUDE_SPEED_EXPERIMENTS_NOT_COMPLETE"' in runner_source
    # Phase-2B per-frame rejection is in pipeline.py (preserved, not weakened).
    assert "CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED" in pipeline_source


def test_unmapped_frame_cannot_reach_stage5_or_create_target(tmp_path):
    from rescue_core.simulation.perception.pipeline import PixelPerceptionPipeline
    from rescue_core.geolocation.pose import PoseHistory
    from ground.backend.app.domain.mission import GeoPoint
    from ground.backend.app.services.coordinates import LocalCoordinateFrame
    from rescue_core.vision.models import DetectionResult
    source = connected_camera(bridge_stream(0, 100))
    frame = source.read()
    detector = SimpleNamespace(model=SimpleNamespace(backend="onnx", sha256="test"))
    pipeline = PixelPerceptionPipeline(
        source=source, detector=detector,
        camera_profile=load_camera_profile(ROOT / "simulation/profiles/ov9281_phase2_oblique_simulation.yaml"),
        pose_history=PoseHistory(), mission_id="test",
        coordinate_frame=LocalCoordinateFrame(GeoPoint(latitude=0, longitude=0)), run_dir=tmp_path,
    )
    pipeline._write_monitor_frame = lambda *args: None
    def forbidden(*args, **kwargs):
        raise AssertionError("Unmapped timestamp reached Stage 5")
    pipeline.geolocator.geolocate = forbidden
    result = DetectionResult(
        frame.frame_id, frame.source_id, frame.capture_timestamp_monotonic_ns,
        frame.capture_timestamp_wall_ns, frame.capture_timestamp_monotonic_ns + 1,
        frame.capture_timestamp_monotonic_ns + 2, "test", "onnx", "MONO8", "RGB",
        [Detection(0, "person", .8, 0, 0, 1, 2, 2, 2)], 0, 1, 0, 2,
    )
    pipeline._on_result(frame, result)
    assert pipeline.metrics.raw_person_detections == 1
    assert pipeline.rejections[0].reason == "CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED"
    assert pipeline.confirmed() == []


# ---------------------------------------------------------------------------
# Phase-2C: DirectSimClockWitness unit tests
# ---------------------------------------------------------------------------

def test_direct_sim_clock_witness_schema_guard_rejects_world_state():
    """Witness records must never carry world-state fields."""
    from rescue_core.simulation.perception.clock import (
        DirectSimClockWitnessRecord, _guard_witness_record,
    )
    import pytest
    record = DirectSimClockWitnessRecord(
        run_id="test", sequence=1,
        gazebo_sim_ns=1_000_000_000, ap_boot_ns=1_000_000_000,
        host_received_ns=100,
    )
    # Valid record should serialize without error.
    d = record.as_dict()
    assert d["gazebo_sim_ns"] == 1_000_000_000
    # Forbidden field should raise.
    with pytest.raises(ValueError, match="SCHEMA_VIOLATION"):
        _guard_witness_record({"attitude": 0.0})
    with pytest.raises(ValueError, match="SCHEMA_VIOLATION"):
        _guard_witness_record({"position": [0, 0, 0]})
    with pytest.raises(ValueError, match="SCHEMA_VIOLATION"):
        _guard_witness_record({"survivor": None})


def test_direct_sim_clock_witness_starts_uninitialized():
    from rescue_core.simulation.perception.clock import (
        DirectSimClockWitness, MappingQuality,
    )
    w = DirectSimClockWitness("test_run")
    assert w.quality() is MappingQuality.UNINITIALIZED
    snap = w.snapshot()
    assert snap["no_world_state"] is True
    assert snap["autonomy_mapping_permitted"] is False
    assert snap["sample_count"] == 0


def test_direct_sim_clock_witness_raises_before_convergence():
    from rescue_core.simulation.perception.clock import DirectSimClockWitness
    import pytest
    w = DirectSimClockWitness("test_run", min_samples=5, min_span_ns=1_000_000_000)
    # No samples yet: should raise.
    with pytest.raises(ValueError, match="DIRECT_SIM_CLOCK_INVALID"):
        w.map_gazebo_to_ap(1_000_000_000)


def test_direct_sim_clock_witness_converges_and_maps():
    """With sufficient samples the witness must produce VALID and map correctly."""
    import time
    from rescue_core.simulation.perception.clock import (
        DirectSimClockWitness, DirectSimCaptureClockMapper, MappingQuality,
    )

    # max_age_ns=60s: prevent test from failing due to sample age.
    # AffineClockMapping max_age_ns defaults to 1 s; we need more for test.
    w = DirectSimClockWitness(
        "test_run",
        min_samples=10,
        min_span_ns=1_000_000_000,
        max_error_ns=50_000_000,
    )
    # Override the internal mapping max_age_ns for test stability.
    w._mapping.max_age_ns = 60_000_000_000  # 60 s
    mapper = DirectSimCaptureClockMapper(w)

    # Simulate lockstep: AP boot time = gazebo sim time + constant offset.
    OFFSET_NS = 5_000_000   # 5 ms constant offset
    # Simulate same-update source-domain pairs (the SIM_JSON clock witness).
    # host_received_ns is wall-clock at receipt, used for window eviction only.
    snapshot_host = time.monotonic_ns()
    for i in range(40):
        gz_ns = 100_000_000 + i * 33_333_333   # 30 fps, starts at 100 ms
        ap_ns = gz_ns + OFFSET_NS
        # Host times: start 2 s ago, end 700 ms ago (in the past at snapshot time).
        host_t = snapshot_host - 2_000_000_000 + i * 33_000_000
        w.observe_source_pair(gz_ns, ap_ns, host_t)


    assert w.quality() is MappingQuality.VALID, f"Expected VALID, got {w.quality()}: {w.snapshot()['mapping']}"
    assert mapper.quality() is MappingQuality.VALID

    test_gz_ns = 100_000_000 + 20 * 33_333_333
    ap_mapped, snap = w.map_gazebo_to_ap(test_gz_ns)
    expected = test_gz_ns + OFFSET_NS
    # Allow 5 ms residual in unit test.
    assert abs(ap_mapped - expected) < 5_000_000, (
        f"Mapped {ap_mapped} too far from expected {expected}: diff {ap_mapped - expected} ns"
    )

    ap_via_mapper, _ = mapper.map_gazebo_to_autopilot(test_gz_ns, time.monotonic_ns())
    assert abs(ap_via_mapper - expected) < 5_000_000



def test_direct_sim_clock_witness_source_pair_domains():
    """observe_source_pair stores the pair as-is; host time is eviction only.

    The pair (gazebo_sim_ns, ap_boot_ns) MUST come from the same FDM update;
    this test only checks the witness records it without latching/externals.
    """
    from rescue_core.simulation.perception.clock import DirectSimClockWitness

    w = DirectSimClockWitness("test_run", min_samples=3, min_span_ns=1_000_000_000)
    base = 1_000_000_000
    rec = w.observe_source_pair(5_000_000_000, 5_000_000_000 + 7_000_000, base)
    assert rec is not None
    assert rec.gazebo_sim_ns == 5_000_000_000
    assert rec.ap_boot_ns == 5_000_000_000 + 7_000_000
    snap = w.snapshot()
    # The pair values must be recorded verbatim — no host-time shaping.
    assert snap["recent_samples"][-1]["ap_boot_ns"] == 5_000_000_000 + 7_000_000
    assert snap["recent_samples"][-1]["gazebo_sim_ns"] == 5_000_000_000


def test_direct_sim_clock_witness_negative_timestamp_rejected():
    """Negative witness timestamps must raise — never pollute the fit."""
    import pytest
    from rescue_core.simulation.perception.clock import DirectSimClockWitness

    w = DirectSimClockWitness("test_run")
    with pytest.raises(ValueError, match="negative witness timestamp"):
        w.observe_source_pair(-1, 0, 0)
    with pytest.raises(ValueError, match="negative witness timestamp"):
        w.observe_source_pair(0, -1, 0)


def test_direct_sim_capture_clock_mapper_fail_closed():
    """Mapper must raise before VALID — never silently return stale mapping."""
    import pytest
    from rescue_core.simulation.perception.clock import (
        DirectSimClockWitness, DirectSimCaptureClockMapper, ClockSyncQuality,
    )
    w = DirectSimClockWitness("test_run")
    mapper = DirectSimCaptureClockMapper(w)
    assert mapper.sync_quality() is ClockSyncQuality.UNSYNCED
    with pytest.raises(ValueError, match="DIRECT_SIM_CLOCK_INVALID"):
        mapper.map_gazebo_to_autopilot(5_000_000_000, 0)
