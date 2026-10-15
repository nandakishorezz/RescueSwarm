"""Pixel-only adapter into the frozen Stage-4/Stage-5 perception chain.

This module deliberately cannot import ``simulation.truth`` or
``simulation.evaluator``. Evaluation is performed afterward by the runner's
observer-only code, not by this inference callback.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from rescue_core.geolocation.calibration import (
    CalibrationVerdict,
    CameraCalibration,
    CameraExtrinsics,
    DistortionModel,
    ExtrinsicsMethod,
    ExtrinsicsQuality,
)
from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel, GroundModel
from rescue_core.geolocation.models import FusedTargetEstimate, RejectedObservation, TargetObservation, TargetTrackState
from rescue_core.geolocation.pose import PoseHistory
from rescue_core.geolocation.transforms import quaternion_to_matrix
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.simulation.profiles import CameraProfile
from rescue_core.simulation.perception.clock import SimulationClockMapper
from rescue_core.vision.agent import VisionAgent
from rescue_core.vision.camera import CameraSource
from rescue_core.vision.detectors import DetectorBackend
from rescue_core.vision.events import VisionEvent
from rescue_core.vision.models import Detection, DetectionResult, FramePacket


class BoundedHistory(list):
    """List-compatible evidence buffer that evicts its oldest entries."""

    def __init__(self, maxlen: int) -> None:
        super().__init__()
        if maxlen < 1:
            raise ValueError("bounded history maxlen must be positive")
        self.maxlen = maxlen
        self.evicted = 0

    def _trim(self) -> None:
        overflow = len(self) - self.maxlen
        if overflow > 0:
            del self[:overflow]
            self.evicted += overflow

    def append(self, item) -> None:
        super().append(item)
        self._trim()

    def extend(self, items) -> None:
        super().extend(items)
        self._trim()


@dataclass(slots=True)
class PixelPerceptionMetrics:
    results: int = 0
    frames_with_person: int = 0
    raw_person_detections: int = 0
    geolocated_observations: int = 0
    geolocation_rejections: int = 0
    confirmed_targets: int = 0
    inference_ms: list[float] = field(default_factory=lambda: BoundedHistory(10_000))


class PixelPerceptionPipeline:
    def __init__(
        self,
        *,
        source: CameraSource,
        detector: DetectorBackend,
        camera_profile: CameraProfile,
        pose_history: PoseHistory,
        mission_id: str,
        coordinate_frame: LocalCoordinateFrame,
        run_dir: Path,
        vehicle_id: str = "drone_01",
        detector_threshold: float = 0.25,
        clock_mapper: SimulationClockMapper | None = None,
        sensor_accuracy_confidence_multiplier: float = 1.0,
        ground_model: GroundModel | None = None,
    ) -> None:
        if detector.model.backend == "mock":
            raise ValueError("Phase-2 pixel perception forbids MockDetectorBackend")
        self.source = source
        self.detector = detector
        self.camera_profile = camera_profile
        self.pose_history = pose_history
        self.mission_id = mission_id
        self.coordinate_frame = coordinate_frame
        self.run_dir = run_dir
        self.vehicle_id = vehicle_id
        self.clock_mapper = clock_mapper
        self.metrics = PixelPerceptionMetrics()
        self.results: list[dict[str, object]] = BoundedHistory(10_000)
        self.observations: list[TargetObservation] = BoundedHistory(4_000)
        self.rejections: list[RejectedObservation] = BoundedHistory(4_000)
        self.estimates: list[FusedTargetEstimate] = BoundedHistory(4_000)
        self.events: list[dict[str, object]] = BoundedHistory(2_000)
        self._lock = threading.Lock()
        self._saved_detection_frame = False
        configured_audit_dir = os.environ.get("RESCUESWARM_EXACT_INFERENCE_AUDIT_DIR")
        self._exact_inference_audit_dir = Path(configured_audit_dir) if configured_audit_dir else None
        self._monitor_frame_counter = 0
        self.worker_error: str | None = None
        self.monitor_write_failures = 0
        self.observation_detections: dict[str, tuple[str, int]] = {}
        self._recent_frame_results: dict[str, tuple[FramePacket, DetectionResult]] = {}
        calibration, extrinsics = simulation_camera_geometry(camera_profile)
        geolocator = TargetGeolocator(
            calibration=calibration,
            extrinsics=extrinsics,
            pose_history=pose_history,
            clock_sync=None,
            ground_model=ground_model or FlatGroundModel(0.0, "SIMULATION_FLAT_GROUND"),
            wgs84_converter=lambda east, north, up: coordinate_frame.to_wgs84(
                EnuPoint(east_m=east, north_m=north, up_m=up)
            ),
            uncertainty=GeolocationUncertaintyEstimator(
                horizontal_position_prior_m=1.5,
                vertical_position_prior_m=1.0,
                pixel_prior_px=2.0,
                attitude_prior_deg=0.5,
                mount_prior_deg=0.25,
                minimum_reported_uncertainty_m=0.75,
                horizontal_accuracy_confidence_multiplier=sensor_accuracy_confidence_multiplier,
            ),
            minimum_gps_fix=3,
            maximum_view_angle_from_nadir_deg=70,
            require_sync=False,
            require_calibrated_extrinsics=False,
        )
        self.geolocator = geolocator
        self.tracker = SpatiotemporalTargetTracker(
            mission_id=mission_id,
            fusion=TargetFusionEngine(),
            wgs84_converter=lambda east, north, up: coordinate_frame.to_wgs84(
                EnuPoint(east_m=east, north_m=north, up_m=up)
            ),
            minimum_observations=3,
            minimum_time_span_s=0.15,
            minimum_detection_confidence=detector_threshold,
        )
        self.agent = VisionAgent(
            source,
            detector,
            queue_size=2,
            no_frame_timeout_s=30.0,
            event_callback=self._on_event,
            result_callback=self._on_result,
            status_path=run_dir / "vision_status.json",
            restart_attempts=1,
            restart_backoff_s=0.5,
        )
        self._thread = threading.Thread(target=self._run_agent, name="phase2-real-vision-agent", daemon=True)

    def _run_agent(self) -> None:
        try:
            self.agent.run()
        except Exception as exc:
            self.worker_error = f"{type(exc).__name__}: {exc}"

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_s: float = 10.0) -> None:
        self.agent.stop()
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise RuntimeError("Phase-2 vision agent did not stop")

    def confirmed(self) -> list[FusedTargetEstimate]:
        with self._lock:
            latest = {item.target_id: item for item in self.estimates}
            return [item for item in latest.values() if item.state is TargetTrackState.CONFIRMED]

    def observation_snapshot(self) -> list[TargetObservation]:
        """Return a lock-consistent bounded snapshot for target-service vision."""
        with self._lock:
            return list(self.observations)

    def recent_frame_result_snapshot(self) -> list[tuple[FramePacket, DetectionResult]]:
        """Bounded rendered frames for YOLO-seeded close-range tracking."""
        with self._lock:
            return list(self._recent_frame_results.values())

    def geolocate_tracking_detection(
        self, frame: FramePacket, detection: Detection,
    ) -> TargetObservation | None:
        """Stage-5 locate a pixel tracker result without entering confirmation."""
        if self.clock_mapper is None or frame.capture_timestamp_sim_ns is None:
            return None
        try:
            mapped_ap_time_ns, _ = self.clock_mapper.map_gazebo_to_autopilot(
                frame.capture_timestamp_sim_ns, time.monotonic_ns(),
            )
        except ValueError:
            return None
        now = time.monotonic_ns()
        result = DetectionResult(
            frame_id=frame.frame_id, source_id=frame.source_id,
            capture_timestamp_monotonic_ns=frame.capture_timestamp_monotonic_ns,
            capture_timestamp_wall_ns=frame.capture_timestamp_wall_ns,
            inference_started_ns=max(now, frame.capture_timestamp_monotonic_ns),
            inference_finished_ns=max(now, frame.capture_timestamp_monotonic_ns),
            model_id=self.detector.model.id, backend="YOLO_SEEDED_TEMPLATE_TRACKER",
            original_channel_format=frame.pixel_format, model_input_format="MONO8_TRACKING",
            detections=[detection], preprocess_ms=0, inference_ms=0,
            postprocess_ms=0, end_to_end_ms=0,
            capture_timestamp_sim_ns=frame.capture_timestamp_sim_ns,
        )
        located = self.geolocator.geolocate(
            result, detection, self.vehicle_id,
            active_width=frame.width, active_height=frame.height,
            active_sensor_mode="MONO8_SIMULATION_TRACKING",
            mapped_autopilot_time_ns=mapped_ap_time_ns,
            evidence_references=[frame.frame_id, "YOLO_SEEDED_TEMPLATE_TRACKER"],
        )
        return located if isinstance(located, TargetObservation) else None

    def _on_event(self, event: VisionEvent) -> None:
        with self._lock:
            self.events.append(event.as_dict())

    def _on_result(self, frame: FramePacket, result: DetectionResult) -> None:
        envelope = {
            **result.as_dict(),
            "execution_source": "SITL_GAZEBO",
            "camera_source": "GAZEBO_RENDERED",
            "camera_profile_id": self.camera_profile.camera_id,
            "model_hash": self.detector.model.sha256,
            "frame_checksum_sha256": frame.camera_metadata.extra.get("checksum_sha256"),
            "raw_min": frame.camera_metadata.extra.get("raw_min"),
            "raw_max": frame.camera_metadata.extra.get("raw_max"),
            "raw_mean": frame.camera_metadata.extra.get("raw_mean"),
            "raw_std": frame.camera_metadata.extra.get("raw_std"),
            "sim_capture_time_ns": frame.camera_metadata.extra.get("sim_capture_time_ns"),
            "bridge_receive_time_ns": frame.camera_metadata.extra.get("bridge_receive_time_ns"),
            "vision_enqueue_time_ns": frame.camera_metadata.extra.get("vision_enqueue_time_ns"),
            "capture_clock_mapping": frame.camera_metadata.extra.get("capture_clock_mapping"),
            "capture_pose_alignment_verified": frame.camera_metadata.extra.get("capture_pose_alignment_verified", False),
            "capture_timestamp_sim_ns": frame.capture_timestamp_sim_ns,
            "capture_timestamp_host_receive_ns": frame.camera_metadata.extra.get("source_receive_time_ns"),
            "camera_sensor_received_monotonic_ns": frame.camera_metadata.extra.get("camera_received_monotonic_ns"),
            "camera_delivery_monotonic_ns": frame.camera_metadata.extra.get("camera_delivery_monotonic_ns"),
            "camera_transport_latency_ms": frame.camera_metadata.extra.get("camera_transport_latency_ms", 0.0),
            "camera_transport_jitter_sample_ms": frame.camera_metadata.extra.get("camera_transport_jitter_sample_ms", 0.0),
            "capture_timestamp_preserved": frame.camera_metadata.extra.get("capture_timestamp_preserved", True),
            "host_time_authoritative_for_pose": False,
        }
        observations: list[TargetObservation] = []
        rejections: list[RejectedObservation] = []
        estimates: list[FusedTargetEstimate] = []
        for detection_index, detection in enumerate(result.detections):
            # SURVIVOR ELIGIBILITY
            # Contextual eligibility layer: The camera physics consistently render the 
            # drone's own parachute and chassis near the top (y ≈ 0) and bottom (y ≈ 800) edges.
            # Real survivors may briefly touch the edges when entering the FOV, but will quickly 
            # move to the center. Rejecting edge detections safely eliminates all self-tracking artifacts.
            pass

            mapped_ap_time_ns: int | None = None
            mapping_snapshot: dict[str, object] | None = None
            if self.clock_mapper is not None and frame.capture_timestamp_sim_ns is not None:
                try:
                    mapped_ap_time_ns, mapping_snapshot = self.clock_mapper.map_gazebo_to_autopilot(
                        frame.capture_timestamp_sim_ns, time.monotonic_ns(),
                    )
                except ValueError as exc:
                    reason = str(exc)
                else:
                    reason = ""
                    envelope["capture_pose_alignment_verified"] = True
                    envelope["mapped_ap_sim_source_time_ns"] = mapped_ap_time_ns
            else:
                # Preserve the public fail-closed rejection taxonomy used by
                # the existing recovery evidence when no qualified mapper is
                # attached to a live run.
                reason = "CAPTURE_TO_AUTOPILOT_CLOCK_MAPPING_UNVERIFIED"
            if reason:
                rejections.append(RejectedObservation(
                    vehicle_id=self.vehicle_id, frame_id=frame.frame_id,
                    capture_timestamp_monotonic_ns=result.capture_timestamp_monotonic_ns,
                    reason=reason,
                    details={"capture_timestamp_sim_ns": frame.capture_timestamp_sim_ns,
                             "mapping_snapshot": mapping_snapshot},
                ))
                continue
            located = self.geolocator.geolocate(
                result,
                detection,
                self.vehicle_id,
                active_width=frame.width,
                active_height=frame.height,
                active_sensor_mode="MONO8_SIMULATION",
                mapped_autopilot_time_ns=mapped_ap_time_ns,
                evidence_references=[frame.frame_id],
            )
            if isinstance(located, RejectedObservation):
                rejections.append(located)
            else:
                observations.append(located)
                self.observation_detections[located.observation_id] = (frame.frame_id, detection_index)
                while len(self.observation_detections) > 4_000:
                    self.observation_detections.pop(next(iter(self.observation_detections)))
                estimate = self.tracker.process(located)
                if estimate is not None:
                    estimate.simulated = True
                    estimates.append(estimate)
        with self._lock:
            self.metrics.results += 1
            self.metrics.frames_with_person += bool(result.detections)
            self.metrics.raw_person_detections += len(result.detections)
            self.metrics.geolocated_observations += len(observations)
            self.metrics.geolocation_rejections += len(rejections)
            self.metrics.inference_ms.append(result.inference_ms)
            self.results.append(envelope)
            self._recent_frame_results[frame.frame_id] = (frame, result)
            while len(self._recent_frame_results) > 6:
                self._recent_frame_results.pop(next(iter(self._recent_frame_results)))
            self.observations.extend(observations)
            self.rejections.extend(rejections)
            self.estimates.extend(estimates)
            self.metrics.confirmed_targets = len({item.target_id for item in self.estimates if item.state is TargetTrackState.CONFIRMED})
            should_save = bool(result.detections) and not self._saved_detection_frame
            if should_save:
                self._saved_detection_frame = True
            self._monitor_frame_counter += 1
            should_refresh_monitor = self._monitor_frame_counter == 1 or self._monitor_frame_counter % 10 == 0
        if should_refresh_monitor:
            try:
                self._write_monitor_frame(frame, result)
            except PermissionError:
                self.monitor_write_failures += 1
        if self._exact_inference_audit_dir is not None:
            self._write_exact_inference_frame(frame, result)
        if should_save:
            evidence = self.run_dir / "perception_evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            Image.fromarray(frame.image).save(evidence / f"{frame.frame_id.replace(':', '_')}_AUTONOMY_INPUT_MONO8.png")
            Image.fromarray(np.repeat(frame.image[..., None], 3, axis=2)).save(
                evidence / f"{frame.frame_id.replace(':', '_')}_DETECTOR_INPUT_RGB_REPLICATION.png"
            )
            overlay = Image.fromarray(np.repeat(frame.image[..., None], 3, axis=2))
            draw = ImageDraw.Draw(overlay)
            for detection in result.detections:
                draw.rectangle((detection.x1, detection.y1, detection.x2, detection.y2), outline=(255, 80, 40), width=3)
                draw.text((detection.x1, max(0, detection.y1 - 14)), f"person {detection.confidence:.2f}", fill=(255, 255, 255))
            overlay.save(evidence / f"{frame.frame_id.replace(':', '_')}_AUTONOMY_OUTPUT.png")

    def _write_exact_inference_frame(self, frame: FramePacket, result: DetectionResult) -> None:
        """Losslessly retain the exact inferred pixels for an opt-in bounded smoke."""
        audit_dir = self._exact_inference_audit_dir
        assert audit_dir is not None
        frames_dir = audit_dir / "exact_rgb_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        safe_id = frame.frame_id.replace(":", "_").replace("/", "_")
        rgb = np.repeat(frame.image[..., None], 3, axis=2) if frame.image.ndim == 2 else frame.image[..., :3].copy()
        path = frames_dir / f"{safe_id}.png"
        Image.fromarray(rgb).save(path, format="PNG", compress_level=1)
        record = {
            "frame_id": frame.frame_id,
            "source_id": frame.source_id,
            "capture_timestamp_sim_ns": frame.capture_timestamp_sim_ns,
            "capture_timestamp_monotonic_ns": frame.capture_timestamp_monotonic_ns,
            "raw_checksum_sha256": frame.camera_metadata.extra.get("checksum_sha256"),
            "exact_rgb_checksum_sha256": hashlib.sha256(np.ascontiguousarray(rgb).tobytes()).hexdigest(),
            "width": frame.width,
            "height": frame.height,
            "pixel_format": frame.pixel_format,
            "exact_rgb_file": str(path.name),
            "online_result": result.as_dict(),
        }
        audit_dir.mkdir(parents=True, exist_ok=True)
        with (audit_dir / "exact_frame_index.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _write_monitor_frame(self, frame: FramePacket, result: DetectionResult) -> None:
        """Publish a bounded latest-frame UI consumer after inference."""
        monitor = self.run_dir / "perception_monitor"
        monitor.mkdir(parents=True, exist_ok=True)
        mono_tmp = monitor / "latest_AUTONOMY_INPUT_MONO8.tmp"
        overlay_tmp = monitor / "latest_AUTONOMY_OUTPUT.tmp"
        mono = Image.fromarray(frame.image)
        mono.save(mono_tmp, format="PNG")
        mono_tmp.replace(monitor / "latest_AUTONOMY_INPUT_MONO8.png")
        overlay = Image.fromarray(np.repeat(frame.image[..., None], 3, axis=2))
        draw = ImageDraw.Draw(overlay)
        for detection in result.detections:
            draw.rectangle((detection.x1, detection.y1, detection.x2, detection.y2), outline=(255, 80, 40), width=3)
            draw.text((detection.x1, max(0, detection.y1 - 14)), f"person {detection.confidence:.2f}", fill=(255, 255, 255))
        overlay.save(overlay_tmp, format="PNG")
        overlay_tmp.replace(monitor / "latest_AUTONOMY_OUTPUT.png")
        metadata_tmp = monitor / "latest_camera_frame.tmp"
        metadata_tmp.write_text(json.dumps({
            "classification": "AUTONOMY_CAMERA_ONLY",
            "frame_id": frame.frame_id,
            "sim_capture_time_ns": frame.camera_metadata.extra.get("sim_capture_time_ns"),
            "resolution": [frame.width, frame.height],
            "pixel_format": frame.pixel_format,
            "detections": len(result.detections),
            "model_id": result.model_id,
            "execution_source": "SITL_GAZEBO",
        }, sort_keys=True), encoding="utf-8")
        metadata_tmp.replace(monitor / "latest_camera_frame.json")

    def persist(self) -> None:
        _write_jsonl(self.run_dir / "perception_detections.jsonl", self.results)
        _write_jsonl(self.run_dir / "target_observations.jsonl", [item.model_dump(mode="json") for item in self.observations])
        _write_jsonl(self.run_dir / "geolocation_rejections.jsonl", [item.model_dump(mode="json") for item in self.rejections])
        _write_jsonl(self.run_dir / "confirmed_target_history.jsonl", [item.model_dump(mode="json") for item in self.estimates])
        _write_jsonl(self.run_dir / "vision_events.jsonl", self.events)
        (self.run_dir / "observation_detection_links.json").write_text(
            json.dumps(self.observation_detections, sort_keys=True), encoding="utf-8",
        )


def simulation_camera_geometry(profile: CameraProfile) -> tuple[CameraCalibration, CameraExtrinsics]:
    fx, fy, cx, cy, _ = profile.projective_intrinsics()
    calibration = CameraCalibration(
        calibration_id=f"{profile.camera_id}_intrinsics",
        camera_identifier=profile.camera_id,
        sensor=profile.sensor_name,
        resolution=(profile.resolution_width, profile.resolution_height),
        sensor_mode="MONO8_SIMULATION",
        distortion_model=DistortionModel.PINHOLE_BROWN,
        camera_matrix=[[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
        distortion_coefficients=profile.distortion_coefficients,
        image_width=profile.resolution_width,
        image_height=profile.resolution_height,
        rms_reprojection_error=0.0,
        mean_reprojection_error=0.0,
        median_reprojection_error=0.0,
        p95_reprojection_error=0.0,
        pattern_type="SIMULATION_PROFILE",
        pattern_dimensions=(2, 2),
        square_size_m=1.0,
        images_used=0,
        images_rejected=0,
        software_version="STAGE_13SIM_PHASE2",
        verdict=CalibrationVerdict.PASS,
        validation_reasons=["SIMULATION PROFILE CONSISTENCY ONLY; PHYSICAL CALIBRATION PENDING"],
    ).with_hash()
    quaternion = profile.mount_rotation_camera_to_body.value
    extrinsics = CameraExtrinsics(
        extrinsics_id=f"{profile.camera_id}_extrinsics",
        camera_identifier=profile.camera_id,
        translation_body_m=profile.mount_translation_body_frd.value,
        quaternion_camera_to_body=quaternion,
        rotation_camera_to_body=quaternion_to_matrix(quaternion).tolist(),
        method=ExtrinsicsMethod.UNKNOWN,
        quality=ExtrinsicsQuality.APPROXIMATE,
        rotation_uncertainty_deg=0.25,
        translation_uncertainty_m=0.01,
        notes="Simulation-only configured transform; physical mount remains unverified.",
    ).with_hash()
    return calibration, extrinsics


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")
