"""Stage 5 synthetic projection/inverse-geolocation source driven by live SITL poses."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np

from rescue_core.autonomy.models import SyntheticSurvivorScenario
from rescue_core.geolocation.calibration import (
    CalibrationVerdict, CameraCalibration, CameraExtrinsics, DistortionModel,
    ExtrinsicsMethod, ExtrinsicsQuality,
)
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.clock_sync import ClockSyncQuality
from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import FusedTargetEstimate, TargetObservation, TargetPixelStrategy
from rescue_core.geolocation.projection import SyntheticTargetProjector
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.vision.models import Detection, DetectionResult

from ..services.coordinates import LocalCoordinateFrame
from .adapter import SITLAutopilotAdapter


def stage7s_synthetic_calibration() -> tuple[CameraCalibration, CameraExtrinsics]:
    calibration = CameraCalibration(
        calibration_id="stage7s_synthetic_pinhole", camera_identifier="SYNTHETIC_TEST_ONLY",
        sensor="SYNTHETIC", resolution=(1280, 800), sensor_mode="SYNTHETIC_1280x800",
        distortion_model=DistortionModel.PINHOLE_BROWN,
        camera_matrix=[[800, 0, 640], [0, 800, 400], [0, 0, 1]],
        distortion_coefficients=[0, 0, 0, 0, 0], image_width=1280, image_height=800,
        rms_reprojection_error=0, mean_reprojection_error=0, median_reprojection_error=0,
        p95_reprojection_error=0, pattern_type="SYNTHETIC", pattern_dimensions=(9, 6),
        square_size_m=.025, images_used=0, images_rejected=0,
        software_version="RescueSwarm Stage 7S synthetic model", verdict=CalibrationVerdict.PASS,
        validation_reasons=["SYNTHETIC TEST CALIBRATION — NOT PHYSICAL EVIDENCE"],
    ).with_hash()
    # Camera optical +Z points body +Z (down); optical +X maps body +Y.
    extrinsics = CameraExtrinsics.from_rotation_matrix(
        extrinsics_id="stage7s_synthetic_nadir", camera_identifier="SYNTHETIC_TEST_ONLY",
        translation_body_m=(0, 0, 0),
        rotation_camera_to_body=np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float),
        method=ExtrinsicsMethod.CALIBRATED, quality=ExtrinsicsQuality.CALIBRATED,
        rotation_uncertainty_deg=.01, translation_uncertainty_m=0,
        notes="SYNTHETIC TEST ONLY",
    )
    return calibration, extrinsics


class SyntheticSearchTargetSource:
    """Produces confirmed targets through the real Stage 5 inverse pipeline."""

    def __init__(
        self, adapter: SITLAutopilotAdapter, mission_id: str, frame: LocalCoordinateFrame,
        scenario: SyntheticSurvivorScenario, vehicle_id: str = "drone_01",
    ):
        if not adapter.pose_subscriber:
            raise RuntimeError("SITL pose subscriber is not connected")
        self.adapter = adapter
        self.subscriber = adapter.pose_subscriber
        self.frame = frame
        self.scenario = scenario
        self.vehicle_id = vehicle_id
        calibration, extrinsics = stage7s_synthetic_calibration()
        self.projector = SyntheticTargetProjector(CameraModel.from_calibration(calibration), extrinsics)
        self.geolocator = TargetGeolocator(
            calibration=calibration, extrinsics=extrinsics,
            pose_history=self.subscriber.history, clock_sync=self.subscriber.clock_sync,
            ground_model=FlatGroundModel(), wgs84_converter=frame.enu_to_wgs84,
            uncertainty=GeolocationUncertaintyEstimator(3, 5, 1, .1, .1, 1),
            pixel_strategy=TargetPixelStrategy.BBOX_CENTER,
        )
        self.tracker = SpatiotemporalTargetTracker(
            mission_id=mission_id, fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
            minimum_observations=3, minimum_time_span_s=.1, association_radius_m=5,
        )
        self.counter = 0
        self.observations: list[TargetObservation] = []
        self.first_detection_at: datetime | None = None
        self.confirmation_at: datetime | None = None

    def update(self) -> tuple[TargetObservation | None, FusedTargetEstimate | None]:
        self.counter += 1
        if self.counter % 5 == 0:
            self.subscriber.request_timesync()
        if self.subscriber.clock_sync.quality is not ClockSyncQuality.SYNCED or not self.subscriber.history.samples:
            return None, None
        sample = self.subscriber.history.samples[-1]
        target = np.array([self.scenario.true_east_m, self.scenario.true_north_m, 0.0])
        pixel = self.projector.project(target, sample)
        if pixel is None:
            return None, None
        camera_origin = np.array([sample.local_position_ned_m[1], sample.local_position_ned_m[0], -sample.local_position_ned_m[2]])
        if np.linalg.norm(target[:2] - camera_origin[:2]) > self.scenario.visibility_distance_m:
            return None, None
        # Seeded probability is reduced to a deterministic periodic decision so
        # integration tests cannot become flaky.
        if self.scenario.detection_probability < 1:
            period = max(1, round(1 / max(self.scenario.detection_probability, .001)))
            if self.counter % period:
                return None, None
        x, y = pixel
        if self.scenario.pixel_noise_stddev:
            rng = np.random.default_rng(self.scenario.seed + self.counter)
            x += float(rng.normal(0, self.scenario.pixel_noise_stddev))
            y += float(rng.normal(0, self.scenario.pixel_noise_stddev))
        # A detector cannot emit a valid person box when the synthetic person is
        # only partially inside the image. Wait for a complete in-frame view.
        if not (4 <= x <= 1276 and 8 <= y <= 792):
            return None, None
        host_ns = sample.autopilot_boot_time_ns - int(self.subscriber.clock_sync.offset_ns or 0)
        detection = Detection(0, "person", .92, x - 4, y - 8, x + 4, y + 8, 1280, 800)
        result = DetectionResult(
            frame_id=f"stage7s:{self.scenario.target_id}:{self.counter:06d}", source_id="stage5_synthetic_projector",
            capture_timestamp_monotonic_ns=host_ns, capture_timestamp_wall_ns=None,
            inference_started_ns=host_ns + 1_000_000, inference_finished_ns=host_ns + 2_000_000,
            model_id="SYNTHETIC_TARGET_PROJECTOR", backend="TEST_ONLY",
            original_channel_format="NONE", model_input_format="NONE", detections=[detection],
            preprocess_ms=0, inference_ms=0, postprocess_ms=0, end_to_end_ms=2,
        )
        observation = self.geolocator.geolocate(
            result, detection, self.vehicle_id, active_width=1280, active_height=800,
            active_sensor_mode="SYNTHETIC_1280x800", evidence_references=[result.frame_id],
        )
        if not isinstance(observation, TargetObservation):
            return None, None
        self.first_detection_at = self.first_detection_at or observation.created_at
        self.observations.append(observation)
        estimate = self.tracker.process(observation)
        if estimate:
            estimate.simulated = True
            estimate.target_id = self.scenario.target_id
            if estimate.state.value == "CONFIRMED":
                self.confirmation_at = self.confirmation_at or datetime.now(UTC)
        return observation, estimate

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": "STAGE5_SYNTHETIC_PROJECTION_AND_GEOLOCATION",
            "observations": len(self.observations),
            "first_detection_at": self.first_detection_at.isoformat() if self.first_detection_at else None,
            "confirmation_at": self.confirmation_at.isoformat() if self.confirmation_at else None,
            "clock_sync": self.subscriber.clock_sync.diagnostics(),
        }
