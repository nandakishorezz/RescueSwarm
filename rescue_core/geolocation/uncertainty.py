"""Honest geolocation uncertainty with explicit evidence and configured priors."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .calibration import CameraCalibration, CameraExtrinsics, ExtrinsicsQuality
from .clock_sync import ClockSyncQuality
from .models import GeolocationQuality
from .pose import InterpolatedPose


@dataclass(frozen=True)
class UncertaintyEstimate:
    uncertainty_m: float
    quality: GeolocationQuality
    components: dict[str, float | str | None]


class GeolocationUncertaintyEstimator:
    def __init__(
        self, horizontal_position_prior_m: float, vertical_position_prior_m: float,
        pixel_prior_px: float, attitude_prior_deg: float, mount_prior_deg: float,
        minimum_reported_uncertainty_m: float,
        horizontal_accuracy_confidence_multiplier: float = 1.0,
    ):
        self.horizontal_prior = horizontal_position_prior_m
        self.vertical_prior = vertical_position_prior_m
        self.pixel_prior = pixel_prior_px
        self.attitude_prior_deg = attitude_prior_deg
        self.mount_prior_deg = mount_prior_deg
        self.minimum = minimum_reported_uncertainty_m
        if horizontal_accuracy_confidence_multiplier < 1:
            raise ValueError("horizontal accuracy confidence multiplier must be at least one")
        self.horizontal_accuracy_confidence_multiplier = horizontal_accuracy_confidence_multiplier

    def estimate(
        self, *, pose: InterpolatedPose, calibration: CameraCalibration,
        extrinsics: CameraExtrinsics, range_m: float, view_angle_from_nadir_deg: float,
        focal_length_px: float, ground_source: str,
    ) -> UncertaintyEstimate:
        sample = pose.sample
        horizontal_raw = sample.horizontal_accuracy_m or self.horizontal_prior
        horizontal = horizontal_raw * self.horizontal_accuracy_confidence_multiplier
        horizontal_source = sample.accuracy_sources.get("horizontal", "CONFIGURED_ASSUMPTION")
        vertical = sample.vertical_accuracy_m or self.vertical_prior
        vertical_source = sample.accuracy_sources.get("vertical", "CONFIGURED_ASSUMPTION")
        reprojection_px = max(calibration.p95_reprojection_error, self.pixel_prior)
        pixel_component = range_m * reprojection_px / max(focal_length_px, 1)
        attitude_component = range_m * math.tan(math.radians(self.attitude_prior_deg))
        mount_deg = extrinsics.rotation_uncertainty_deg or self.mount_prior_deg
        mount_component = range_m * math.tan(math.radians(mount_deg))
        vertical_geometry = vertical * math.tan(math.radians(min(view_angle_from_nadir_deg, 89)))
        pose_age_component = 0.0
        if sample.velocity_ned_m_s:
            speed = math.hypot(sample.velocity_ned_m_s[0], sample.velocity_ned_m_s[1])
            pose_age_component = speed * pose.diagnostics.nearest_sample_age_ms / 1000
        sync_component = 0.0
        if pose.diagnostics.clock_sync_quality in {ClockSyncQuality.UNSYNCED, ClockSyncQuality.DEGRADED}:
            sync_component = max(0.5, pose_age_component)
        rss = math.sqrt(sum(value * value for value in (
            horizontal, pixel_component, attitude_component, mount_component,
            vertical_geometry, pose_age_component, sync_component,
        )))
        uncertainty = max(self.minimum, rss)
        if extrinsics.quality is ExtrinsicsQuality.UNKNOWN:
            quality = GeolocationQuality.LOW
        elif horizontal_source == "CONFIGURED_ASSUMPTION":
            quality = GeolocationQuality.STANDARD_GNSS_LIMITED
        elif uncertainty <= 0.5 and extrinsics.quality is ExtrinsicsQuality.CALIBRATED:
            quality = GeolocationQuality.PRECISION
        elif uncertainty <= 2 and extrinsics.quality is ExtrinsicsQuality.CALIBRATED:
            quality = GeolocationQuality.GOOD
        else:
            quality = GeolocationQuality.STANDARD_GNSS_LIMITED
        return UncertaintyEstimate(uncertainty, quality, {
            "gnss_horizontal_m": horizontal, "gnss_horizontal_raw_m": horizontal_raw,
            "gnss_horizontal_confidence_multiplier": self.horizontal_accuracy_confidence_multiplier,
            "gnss_horizontal_source": horizontal_source,
            "vertical_m": vertical, "vertical_source": vertical_source,
            "calibration_pixel_m": pixel_component, "attitude_m": attitude_component,
            "mount_m": mount_component, "vertical_geometry_m": vertical_geometry,
            "pose_age_m": pose_age_component, "clock_sync_m": sync_component,
            "common_bias_floor_m": horizontal, "ground_source": ground_source,
        })


class MonteCarloUncertaintyEvaluator:
    """Optional offline spread analysis; never runs in the per-frame path."""

    def __init__(self, samples: int = 1000, seed: int = 42):
        if samples < 10:
            raise ValueError("Monte Carlo evaluation requires at least 10 samples")
        self.samples = samples
        self.seed = seed

    def evaluate(self, sampler: object) -> dict[str, object]:
        rng = np.random.default_rng(self.seed)
        points = np.asarray([sampler(rng) for _ in range(self.samples)], dtype=float)  # type: ignore[operator]
        if points.shape != (self.samples, 2) or not np.isfinite(points).all():
            raise ValueError("Monte Carlo sampler must return finite east/north coordinates")
        center = np.median(points, axis=0)
        radii = np.linalg.norm(points - center, axis=1)
        return {
            "samples": self.samples, "seed": self.seed,
            "median_east_m": float(center[0]), "median_north_m": float(center[1]),
            "radial_p50_m": float(np.percentile(radii, 50)),
            "radial_p95_m": float(np.percentile(radii, 95)),
            "covariance_m2": np.cov(points.T).tolist(),
        }
