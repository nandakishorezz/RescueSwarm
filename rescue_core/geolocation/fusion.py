"""Lightweight world-space association, robust fusion, and survivor confirmation."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from .models import FusedTargetEstimate, GeolocationQuality, TargetObservation, TargetTrackState


class TargetFusionEngine:
    """Median/MAD spatial gate followed by uncertainty-dominated weighted mean."""

    def __init__(self, outlier_mad_scale: float = 3.5, minimum_outlier_gate_m: float = 2, minimum_uncertainty_m: float = 1):
        if outlier_mad_scale <= 0 or minimum_outlier_gate_m <= 0 or minimum_uncertainty_m <= 0:
            raise ValueError("Fusion thresholds must be positive")
        self.outlier_mad_scale = outlier_mad_scale
        self.minimum_gate = minimum_outlier_gate_m
        self.minimum_uncertainty = minimum_uncertainty_m

    def fuse(
        self, mission_id: str, target_id: str, observations: list[TargetObservation],
        wgs84_converter: Any, state: TargetTrackState,
    ) -> FusedTargetEstimate:
        if not observations:
            raise ValueError("Cannot fuse an empty observation list")
        points = np.array([[obs.target_east_m, obs.target_north_m] for obs in observations], dtype=float)
        coordinate_median = np.median(points, axis=0)
        distances = np.linalg.norm(points - coordinate_median, axis=1)
        distance_median = float(np.median(distances))
        mad = float(np.median(np.abs(distances - distance_median)))
        robust_sigma = 1.4826 * mad
        gate = max(self.minimum_gate, distance_median + self.outlier_mad_scale * robust_sigma)
        inlier_mask = distances <= gate
        # Preserve at least the strongest spatial consensus in degenerate small sets.
        if not inlier_mask.any():
            inlier_mask[int(np.argmin(distances))] = True
        inliers = [obs for obs, accepted in zip(observations, inlier_mask, strict=True) if accepted]
        outliers = [obs for obs, accepted in zip(observations, inlier_mask, strict=True) if not accepted]
        uncertainties = np.array([max(obs.position_uncertainty_m, self.minimum_uncertainty) for obs in inliers])
        # Detection confidence only makes a small bounded adjustment; spatial uncertainty dominates.
        confidence_factors = np.array([0.75 + 0.25 * obs.detection_confidence for obs in inliers])
        weights = confidence_factors / np.square(uncertainties)
        inlier_points = np.array([[obs.target_east_m, obs.target_north_m] for obs in inliers])
        fused = np.average(inlier_points, axis=0, weights=weights)
        residuals = np.linalg.norm(inlier_points - fused, axis=1)
        weighted_residual = math.sqrt(float(np.average(np.square(residuals), weights=weights))) if len(inliers) > 1 else 0.0
        common_bias_floor = max(
            self.minimum_uncertainty,
            max(float(obs.uncertainty_components.get("common_bias_floor_m") or 0) for obs in inliers),
        )
        independent = float(statistics.median(uncertainties)) / math.sqrt(len(inliers))
        fused_uncertainty = max(common_bias_floor, self.minimum_uncertainty, math.hypot(independent, weighted_residual))
        wgs84 = wgs84_converter(float(fused[0]), float(fused[1]), 0.0)
        evidence = sorted({item for obs in inliers for item in obs.evidence_references})
        qualities = [obs.quality for obs in inliers]
        quality = min(qualities, key=lambda value: list(GeolocationQuality).index(value))
        return FusedTargetEstimate(
            target_id=target_id, mission_id=mission_id, state=state,
            east_m=float(fused[0]), north_m=float(fused[1]),
            latitude=float(wgs84.latitude), longitude=float(wgs84.longitude),
            uncertainty_m=fused_uncertainty,
            detection_confidence=float(statistics.median(obs.detection_confidence for obs in inliers)),
            observation_count=len(observations), inlier_count=len(inliers), outlier_count=len(outliers),
            first_seen_at=min(obs.created_at for obs in observations),
            last_seen_at=max(obs.created_at for obs in observations),
            assigned_vehicle_id=inliers[0].vehicle_id if len({obs.vehicle_id for obs in inliers}) == 1 else None,
            evidence_references=evidence,
            inlier_observation_ids=[obs.observation_id for obs in inliers],
            outlier_observation_ids=[obs.observation_id for obs in outliers],
            camera_calibration_ids=sorted({obs.camera_calibration_id for obs in inliers}),
            extrinsics_ids=sorted({obs.extrinsics_id for obs in inliers}),
            quality=quality,
        )


@dataclass
class _Track:
    target_id: str
    observations: list[TargetObservation] = field(default_factory=list)
    state: TargetTrackState = TargetTrackState.CANDIDATE
    estimate: FusedTargetEstimate | None = None
    superseded_by: str | None = None


class SpatiotemporalTargetTracker:
    """World-space association first; short-term pixel proximity is a fallback."""

    def __init__(
        self, *, mission_id: str, fusion: TargetFusionEngine, wgs84_converter: Any,
        minimum_observations: int = 3, minimum_time_span_s: float = 0.15,
        time_window_s: float = 8, association_radius_m: float = 6,
        minimum_detection_confidence: float = 0.25,
        maximum_geolocation_uncertainty_m: float = 20,
        short_term_time_s: float = 1.0, short_term_pixel_radius_px: float = 50.0,
        association_sigma: float = 2.5,
        confirmed_reacquisition_radius_m: float | None = None,
        maximum_tracks: int = 256, maximum_observations_per_track: int = 500,
    ):
        self.mission_id = mission_id
        self.fusion = fusion
        self.wgs84_converter = wgs84_converter
        self.minimum_observations = minimum_observations
        self.minimum_time_span_s = minimum_time_span_s
        self.time_window_s = time_window_s
        self.association_radius_m = association_radius_m
        self.minimum_confidence = minimum_detection_confidence
        self.maximum_uncertainty = maximum_geolocation_uncertainty_m
        self.short_term_time_s = short_term_time_s
        self.short_term_pixel_radius_px = short_term_pixel_radius_px
        self.association_sigma = association_sigma
        self.confirmed_reacquisition_radius_m = (
            confirmed_reacquisition_radius_m
            if confirmed_reacquisition_radius_m is not None
            else association_radius_m
        )
        self.maximum_tracks = maximum_tracks
        self.maximum_observations_per_track = maximum_observations_per_track
        self.tracks: dict[str, _Track] = {}
        self.aliases: dict[str, str] = {}
        self.merged_events: list[tuple[str, str]] = []

    def canonical_id(self, target_id: str) -> str:
        """Resolve any alias chain to the canonical active target ID."""
        current = target_id
        visited = set()
        while current in self.aliases and current not in visited:
            visited.add(current)
            current = self.aliases[current]
        return current

    def process(
        self, observation: TargetObservation, *, excluded_track_ids: set[str] | None = None,
    ) -> FusedTargetEstimate | None:
        if observation.detection_confidence < self.minimum_confidence:
            return None
        if observation.position_uncertainty_m > self.maximum_uncertainty:
            return None

        # Resolve incoming observation alias if previously assigned
        if observation.target_track_id:
            observation.target_track_id = self.canonical_id(observation.target_track_id)

        # Camera/WebSocket delivery is at-least-once. Replaying an observation
        # must neither inflate evidence nor create a competing survivor record.
        for existing in self.tracks.values():
            if any(item.observation_id == observation.observation_id for item in existing.observations):
                canonical_target_id = self.canonical_id(existing.target_id)
                observation.target_track_id = canonical_target_id
                target_track = self.tracks.get(canonical_target_id)
                return target_track.estimate if target_track else existing.estimate

        raw_excluded = excluded_track_ids or set()
        excluded = {self.canonical_id(t_id) for t_id in raw_excluded}

        # Ultralytics can occasionally retain multiple overlapping boxes for
        # one person in a single frame. Collapse them before they can start a
        # competing persistent identity or double-count evidence.
        duplicate = self._same_frame_duplicate(observation, excluded)
        if duplicate is not None:
            canonical_dup_id = self.canonical_id(duplicate.target_id)
            observation.target_track_id = canonical_dup_id
            target_track = self.tracks.get(canonical_dup_id)
            return target_track.estimate if target_track else duplicate.estimate

        track = self._associate(observation, excluded)
        if track is None:
            if len(self.tracks) >= self.maximum_tracks:
                stale_candidates = [
                    item for item in self.tracks.values()
                    if item.state is not TargetTrackState.CONFIRMED and item.superseded_by is None
                ]
                victim = min(
                    stale_candidates or list(self.tracks.values()),
                    key=lambda item: item.observations[-1].created_at if item.observations else datetime.min.replace(tzinfo=UTC),
                )
                del self.tracks[victim.target_id]
            track = _Track(target_id=str(uuid5(
                NAMESPACE_URL, f"{self.mission_id}:target:{len(self.tracks) + 1}"
            )))
            self.tracks[track.target_id] = track

        observation.target_track_id = track.target_id
        track.observations.append(observation)
        if len(track.observations) > self.maximum_observations_per_track:
            del track.observations[:-self.maximum_observations_per_track]
        timespan_wall = (
            max(item.created_at for item in track.observations)
            - min(item.created_at for item in track.observations)
        ).total_seconds()
        timespan_mono = (
            max(item.capture_timestamp_monotonic_ns for item in track.observations)
            - min(item.capture_timestamp_monotonic_ns for item in track.observations)
        ) / 1e9
        timespan = max(timespan_wall, timespan_mono)

        track.estimate = self.fusion.fuse(
            self.mission_id, track.target_id, track.observations, self.wgs84_converter, track.state
        )

        if track.state == TargetTrackState.CANDIDATE:
            inliers = track.estimate.inlier_count
            track_inliers = [
                obs for obs in track.observations
                if obs.observation_id in track.estimate.inlier_observation_ids
            ]
            distinct_temporal = len({
                (
                    obs.frame_id,
                    obs.capture_timestamp_monotonic_ns if obs.capture_timestamp_monotonic_ns > 0 else None,
                    int(obs.created_at.timestamp() * 1000) // 50,
                )
                for obs in track_inliers
            })

            # Require sufficient independent evidence across distinct frames and time
            if (
                inliers >= self.minimum_observations
                and distinct_temporal >= self.minimum_observations
                and timespan >= self.minimum_time_span_s
            ):
                def is_border_locked(obs: TargetObservation) -> bool:
                    x1, y1, x2, y2 = obs.bbox_original_px
                    return x1 < 15.0 or y1 < 15.0 or x2 > 1265.0 or y2 > 785.0

                if not all(is_border_locked(obs) for obs in track_inliers):
                    if not self._is_drone_shadow_or_moving_artifact(track):
                        track.state = TargetTrackState.CONFIRMED
                        track.estimate.state = TargetTrackState.CONFIRMED
                    else:
                        track.state = TargetTrackState.REJECTED
                        if track.estimate:
                            track.estimate.state = TargetTrackState.REJECTED

        # Check if the updated track can be merged into or with another track representing the same person
        merged_track = self._try_merge_tracks(track)
        if merged_track is not None:
            track = merged_track
            observation.target_track_id = track.target_id

        return track.estimate

    def _is_drone_shadow_or_moving_artifact(self, track: _Track) -> bool:
        """Reject tracks that move consistently with the vehicle or stay geometrically locked to vehicle shadow."""
        if len(track.observations) < 2:
            return False

        obs_start = track.observations[0]
        obs_end = track.observations[-1]

        drone_dx = obs_end.camera_origin_enu_m[0] - obs_start.camera_origin_enu_m[0]
        drone_dy = obs_end.camera_origin_enu_m[1] - obs_start.camera_origin_enu_m[1]
        drone_disp = math.hypot(drone_dx, drone_dy)

        target_dx = obs_end.target_east_m - obs_start.target_east_m
        target_dy = obs_end.target_north_m - obs_start.target_north_m
        target_disp = math.hypot(target_dx, target_dy)

        pixel_dx = obs_end.chosen_pixel[0] - obs_start.chosen_pixel[0]
        pixel_dy = obs_end.chosen_pixel[1] - obs_start.chosen_pixel[1]
        pixel_disp = math.hypot(pixel_dx, pixel_dy)

        # Case 1: Vehicle is in flight transit (drone_disp >= 0.8m)
        if drone_disp >= 0.8:
            relative_target_drone_drift = math.hypot(target_dx - drone_dx, target_dy - drone_dy)
            # A shadow moves along with the drone across the ground
            if relative_target_drone_drift <= 0.40 * drone_disp:
                return True
            # In image space: shadow stays at the same pixel while ground coordinates move with the drone
            if target_disp >= 0.50 * drone_disp and pixel_disp <= 50.0:
                return True

        return False

    def _same_frame_duplicate(
        self, observation: TargetObservation, excluded_track_ids: set[str],
    ) -> _Track | None:
        candidate_ids = set(excluded_track_ids)
        for t in self.tracks.values():
            if t.state is not TargetTrackState.REJECTED and t.superseded_by is None:
                for obs in t.observations:
                    if (
                        obs.frame_id == observation.frame_id
                        and abs((obs.created_at - observation.created_at).total_seconds()) <= 0.05
                    ):
                        candidate_ids.add(t.target_id)

        for target_id in candidate_ids:
            canonical_target_id = self.canonical_id(target_id)
            track = self.tracks.get(canonical_target_id)
            if track is None or track.state is TargetTrackState.REJECTED or track.superseded_by is not None:
                continue
            for prior in reversed(track.observations):
                if prior.frame_id != observation.frame_id:
                    continue
                if abs((prior.created_at - observation.created_at).total_seconds()) > 0.05:
                    continue
                if (
                    prior.capture_timestamp_monotonic_ns > 0
                    and observation.capture_timestamp_monotonic_ns > 0
                    and abs(prior.capture_timestamp_monotonic_ns - observation.capture_timestamp_monotonic_ns) > 50_000_000
                ):
                    continue
                world_distance = math.hypot(
                    observation.target_east_m - prior.target_east_m,
                    observation.target_north_m - prior.target_north_m,
                )
                pixel_distance = math.hypot(
                    observation.chosen_pixel[0] - prior.chosen_pixel[0],
                    observation.chosen_pixel[1] - prior.chosen_pixel[1],
                )
                c1_x = (observation.bbox_original_px[0] + observation.bbox_original_px[2]) / 2.0
                c1_y = (observation.bbox_original_px[1] + observation.bbox_original_px[3]) / 2.0
                c2_x = (prior.bbox_original_px[0] + prior.bbox_original_px[2]) / 2.0
                c2_y = (prior.bbox_original_px[1] + prior.bbox_original_px[3]) / 2.0
                center_distance = math.hypot(c1_x - c2_x, c1_y - c2_y)

                left = max(observation.bbox_original_px[0], prior.bbox_original_px[0])
                top = max(observation.bbox_original_px[1], prior.bbox_original_px[1])
                right = min(observation.bbox_original_px[2], prior.bbox_original_px[2])
                bottom = min(observation.bbox_original_px[3], prior.bbox_original_px[3])
                intersection = max(0.0, right - left) * max(0.0, bottom - top)
                observation_area = max(0.0, observation.bbox_original_px[2] - observation.bbox_original_px[0]) * max(
                    0.0, observation.bbox_original_px[3] - observation.bbox_original_px[1]
                )
                prior_area = max(0.0, prior.bbox_original_px[2] - prior.bbox_original_px[0]) * max(
                    0.0, prior.bbox_original_px[3] - prior.bbox_original_px[1]
                )
                union = observation_area + prior_area - intersection
                bbox_iou = intersection / union if union > 0 else 0.0
                smaller_area = min(observation_area, prior_area)
                bbox_containment = intersection / smaller_area if smaller_area > 0 else 0.0
                larger_diagonal = max(
                    math.hypot(observation.bbox_original_px[2] - observation.bbox_original_px[0], observation.bbox_original_px[3] - observation.bbox_original_px[1]),
                    math.hypot(prior.bbox_original_px[2] - prior.bbox_original_px[0], prior.bbox_original_px[3] - prior.bbox_original_px[1]),
                )

                if (
                    (bbox_iou >= 0.35 and center_distance <= 0.6 * larger_diagonal)
                    or (bbox_containment >= 0.50 and center_distance <= 0.7 * larger_diagonal)
                    or (world_distance <= 0.45 and pixel_distance <= 35.0)
                ):
                    return track
        return None

    def _associate(self, observation: TargetObservation, excluded_track_ids: set[str] | None = None) -> _Track | None:
        candidates: list[tuple[float, float, _Track]] = []
        excluded = {self.canonical_id(t_id) for t_id in (excluded_track_ids or set())}
        for track in self.tracks.values():
            if track.target_id in excluded or track.superseded_by is not None:
                continue
            if not track.observations:
                continue
            last = track.observations[-1]
            age_s = abs((observation.created_at - last.created_at).total_seconds())
            confirmed = track.state is TargetTrackState.CONFIRMED
            rejected = track.state is TargetTrackState.REJECTED

            # Confirmed tracks represent static ground targets in search-and-rescue;
            # they remain re-associable across the full mission. Candidate tracks
            # maintain association across neighboring sweep passes.
            # Rejected tracks retain short-term association to absorb trailing shadow detections.
            if not confirmed:
                candidate_retention = max(self.time_window_s * 4, 120.0) if not rejected else (self.short_term_time_s * 3.0)
                if age_s > candidate_retention:
                    continue

            reference_e = track.estimate.east_m if track.estimate else last.target_east_m
            reference_n = track.estimate.north_m if track.estimate else last.target_north_m
            fused_distance = math.hypot(
                observation.target_east_m - reference_e,
                observation.target_north_m - reference_n,
            )
            last_observation_distance = math.hypot(
                observation.target_east_m - last.target_east_m,
                observation.target_north_m - last.target_north_m,
            )
            distance = (
                min(fused_distance, last_observation_distance)
                if age_s <= self.short_term_time_s
                else fused_distance
            )
            reference_uncertainty = max(
                last.position_uncertainty_m,
                track.estimate.uncertainty_m if track.estimate else last.position_uncertainty_m,
            )
            uncertainty_overlap = math.hypot(
                observation.position_uncertainty_m,
                reference_uncertainty,
            )
            sigma = max(3.0, self.association_sigma) if confirmed else self.association_sigma
            physical_ceiling = (
                self.confirmed_reacquisition_radius_m if confirmed else self.association_radius_m
            )
            uncertainty_gate = sigma * uncertainty_overlap
            association_gate = min(physical_ceiling, max(uncertainty_gate, 2.0))
            if distance > association_gate:
                continue

            image_penalty = 0.0
            if age_s <= self.short_term_time_s:
                last_x = (last.bbox_original_px[0] + last.bbox_original_px[2]) / 2
                last_y = (last.bbox_original_px[1] + last.bbox_original_px[3]) / 2
                current_x = (observation.bbox_original_px[0] + observation.bbox_original_px[2]) / 2
                current_y = (observation.bbox_original_px[1] + observation.bbox_original_px[3]) / 2
                image_distance = math.hypot(current_x - last_x, current_y - last_y)
                # Projection jitter can move one person's ground estimate by
                # metres between adjacent camera frames.  A continuous image
                # tracklet is stronger evidence than that short-term shift.
                image_penalty = min(4.0, image_distance / self.short_term_pixel_radius_px)

            if distance <= association_gate:
                penalty = image_penalty + (5.0 if rejected else 0.0)
                candidates.append((distance / max(association_gate, 1e-6) + penalty, distance, track))
                continue
        return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None

    def _have_cooccurring_frames(self, t1: _Track, t2: _Track) -> bool:
        """Check if two tracks appeared simultaneously in any camera frame as distinct persons."""
        t1_frames = {obs.frame_id: obs for obs in t1.observations}
        for obs2 in t2.observations:
            obs1 = t1_frames.get(obs2.frame_id)
            if obs1 is not None:
                if abs((obs1.created_at - obs2.created_at).total_seconds()) > 0.05:
                    continue
                if (
                    obs1.capture_timestamp_monotonic_ns > 0
                    and obs2.capture_timestamp_monotonic_ns > 0
                    and abs(obs1.capture_timestamp_monotonic_ns - obs2.capture_timestamp_monotonic_ns) > 50_000_000
                ):
                    continue
                left = max(obs1.bbox_original_px[0], obs2.bbox_original_px[0])
                top = max(obs1.bbox_original_px[1], obs2.bbox_original_px[1])
                right = min(obs1.bbox_original_px[2], obs2.bbox_original_px[2])
                bottom = min(obs1.bbox_original_px[3], obs2.bbox_original_px[3])
                intersection = max(0.0, right - left) * max(0.0, bottom - top)
                area1 = max(0.0, obs1.bbox_original_px[2] - obs1.bbox_original_px[0]) * max(
                    0.0, obs1.bbox_original_px[3] - obs1.bbox_original_px[1]
                )
                area2 = max(0.0, obs2.bbox_original_px[2] - obs2.bbox_original_px[0]) * max(
                    0.0, obs2.bbox_original_px[3] - obs2.bbox_original_px[1]
                )
                union = area1 + area2 - intersection
                iou = intersection / union if union > 0 else 0.0
                smaller = min(area1, area2)
                containment = intersection / smaller if smaller > 0 else 0.0

                world_dist = math.hypot(
                    obs1.target_east_m - obs2.target_east_m,
                    obs1.target_north_m - obs2.target_north_m,
                )
                c1_x = (obs1.bbox_original_px[0] + obs1.bbox_original_px[2]) / 2.0
                c1_y = (obs1.bbox_original_px[1] + obs1.bbox_original_px[3]) / 2.0
                c2_x = (obs2.bbox_original_px[0] + obs2.bbox_original_px[2]) / 2.0
                c2_y = (obs2.bbox_original_px[1] + obs2.bbox_original_px[3]) / 2.0
                pixel_dist = math.hypot(c1_x - c2_x, c1_y - c2_y)
                smaller_diagonal = min(
                    math.hypot(obs1.bbox_original_px[2] - obs1.bbox_original_px[0], obs1.bbox_original_px[3] - obs1.bbox_original_px[1]),
                    math.hypot(obs2.bbox_original_px[2] - obs2.bbox_original_px[0], obs2.bbox_original_px[3] - obs2.bbox_original_px[1]),
                )
                larger_diagonal = max(
                    math.hypot(obs1.bbox_original_px[2] - obs1.bbox_original_px[0], obs1.bbox_original_px[3] - obs1.bbox_original_px[1]),
                    math.hypot(obs2.bbox_original_px[2] - obs2.bbox_original_px[0], obs2.bbox_original_px[3] - obs2.bbox_original_px[1]),
                )

                # Pixel separation alone is weak evidence: the accepted model
                # sometimes returns separate boxes for parts of one person.
                # Protect genuinely separate people when ground and image
                # measurements agree, or when the image gap is unambiguous.
                if (
                    (iou < 0.5 and world_dist >= 0.85 and pixel_dist >= 0.35 * smaller_diagonal)
                    or (iou < 0.15 and containment < 0.25 and pixel_dist >= max(35.0, 1.5 * larger_diagonal))
                ):
                    return True
        return False

    @staticmethod
    def _visual_handoff_count(t1: _Track, t2: _Track) -> int:
        """Count short, alternating image-track handoffs between two IDs.

        Geography alone cannot distinguish two nearby people.  Two switches
        along the same image trajectory provide positive continuity evidence;
        a single coincident position does not.
        """
        recent = sorted(
            [(obs.created_at, 0, obs) for obs in t1.observations[-40:]]
            + [(obs.created_at, 1, obs) for obs in t2.observations[-40:]],
            key=lambda item: item[0],
        )
        handoffs = 0
        for (_, first_id, first), (_, second_id, second) in zip(recent, recent[1:]):
            if first_id == second_id or first.frame_id == second.frame_id:
                continue
            age_s = (second.created_at - first.created_at).total_seconds()
            if not 0 < age_s <= 0.75:
                continue
            ax = (first.bbox_original_px[0] + first.bbox_original_px[2]) / 2
            ay = (first.bbox_original_px[1] + first.bbox_original_px[3]) / 2
            bx = (second.bbox_original_px[0] + second.bbox_original_px[2]) / 2
            by = (second.bbox_original_px[1] + second.bbox_original_px[3]) / 2
            scale = max(
                math.hypot(first.bbox_original_px[2] - first.bbox_original_px[0], first.bbox_original_px[3] - first.bbox_original_px[1]),
                math.hypot(second.bbox_original_px[2] - second.bbox_original_px[0], second.bbox_original_px[3] - second.bbox_original_px[1]),
            )
            if scale > 0 and math.hypot(ax - bx, ay - by) <= scale:
                handoffs += 1
        return handoffs

    @staticmethod
    def _has_coincident_box(t1: _Track, t2: _Track) -> bool:
        """Recognize duplicate delivery of a box with a different frame ID."""
        for first in t1.observations[-12:]:
            for second in t2.observations[-12:]:
                if abs((first.created_at - second.created_at).total_seconds()) > 0.05:
                    continue
                a, b = first.bbox_original_px, second.bbox_original_px
                intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
                area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
                union = area_a + area_b - intersection
                if union > 0 and intersection / union >= 0.9:
                    return True
        return False

    def _try_merge_tracks(self, target_track: _Track) -> _Track | None:
        """Merge tracks conclusively determined to represent the same person into one canonical identity."""
        if target_track.state is TargetTrackState.REJECTED or target_track.superseded_by is not None:
            return None

        canonical_id = self.canonical_id(target_track.target_id)
        current = self.tracks.get(canonical_id)
        if current is None or current.state is TargetTrackState.REJECTED or current.superseded_by is not None:
            return None

        e1 = current.estimate.east_m if current.estimate else current.observations[-1].target_east_m
        n1 = current.estimate.north_m if current.estimate else current.observations[-1].target_north_m
        unc1 = current.estimate.uncertainty_m if current.estimate else 2.0

        for other_id, other in list(self.tracks.items()):
            if other_id == current.target_id or other.state is TargetTrackState.REJECTED or other.superseded_by is not None:
                continue

            # Two distinct people detected in the same frame must never be merged
            if self._have_cooccurring_frames(current, other):
                continue

            e2 = other.estimate.east_m if other.estimate else other.observations[-1].target_east_m
            n2 = other.estimate.north_m if other.estimate else other.observations[-1].target_north_m
            unc2 = other.estimate.uncertainty_m if other.estimate else 2.0
            dist = math.hypot(e1 - e2, n1 - n2)

            # Distance is only a plausibility gate.  It is not identity proof:
            # two independently observed people can be just one metre apart.
            should_merge = False
            combined_unc = math.hypot(unc1, unc2)
            visual_handoffs = self._visual_handoff_count(current, other)
            if current.state is TargetTrackState.CONFIRMED or other.state is TargetTrackState.CONFIRMED:
                reacq_gate = min(
                    self.confirmed_reacquisition_radius_m,
                    max(self.association_radius_m, 3.0 * combined_unc),
                )
                if dist <= reacq_gate:
                    # One track is CONFIRMED and the other is a newly formed CANDIDATE:
                    # Having never co-occurred as distinct people, they represent the same survivor
                    # reacquired across search/delivery interruptions.
                    if (
                        (current.state is TargetTrackState.CONFIRMED and other.state is TargetTrackState.CANDIDATE)
                        or (other.state is TargetTrackState.CONFIRMED and current.state is TargetTrackState.CANDIDATE)
                        or (current.state is TargetTrackState.CONFIRMED and other.state is TargetTrackState.CONFIRMED)
                    ):
                        should_merge = True
                    elif visual_handoffs >= 2 or (dist <= 0.8 and self._has_coincident_box(current, other)):
                        should_merge = True
            elif current.state is TargetTrackState.CANDIDATE and other.state is TargetTrackState.CANDIDATE:
                candidate_gate = min(
                    self.association_radius_m,
                    max(2.5 * combined_unc, 2.5),
                )
                if dist <= candidate_gate and (
                    visual_handoffs >= 2 or (dist <= 0.8 and self._has_coincident_box(current, other))
                ):
                    should_merge = True

            if should_merge:
                current = self._merge_tracks(current, other)
                e1 = current.estimate.east_m if current.estimate else current.observations[-1].target_east_m
                n1 = current.estimate.north_m if current.estimate else current.observations[-1].target_north_m
                unc1 = current.estimate.uncertainty_m if current.estimate else 2.0

        return current

    def _merge_tracks(self, t1: _Track, t2: _Track) -> _Track:
        # An already-confirmed survivor ID is durable.  Newer tracks can add
        # observations but cannot replace that identity merely by accumulating
        # more frames before reconciliation.
        if t1.state is TargetTrackState.CONFIRMED and t2.state is not TargetTrackState.CONFIRMED:
            canonical, superseded = t1, t2
        elif t2.state is TargetTrackState.CONFIRMED and t1.state is not TargetTrackState.CONFIRMED:
            canonical, superseded = t2, t1
        elif t1.state is TargetTrackState.CONFIRMED and t2.state is TargetTrackState.CONFIRMED:
            canonical, superseded = sorted(
                (t1, t2), key=lambda track: (
                    track.estimate.first_seen_at if track.estimate else min(obs.created_at for obs in track.observations),
                    track.target_id,
                )
            )
        elif len(t1.observations) > len(t2.observations):
            canonical, superseded = t1, t2
        elif len(t2.observations) > len(t1.observations):
            canonical, superseded = t2, t1
        else:
            canonical, superseded = t1, t2

        self.aliases[superseded.target_id] = canonical.target_id
        for src, dst in list(self.aliases.items()):
            if dst == superseded.target_id:
                self.aliases[src] = canonical.target_id

        superseded.superseded_by = canonical.target_id
        superseded.state = TargetTrackState.REJECTED
        if superseded.estimate:
            superseded.estimate.state = TargetTrackState.REJECTED

        self.merged_events.append((superseded.target_id, canonical.target_id))

        existing_obs_ids = {obs.observation_id for obs in canonical.observations}
        existing_frame_ids = {obs.frame_id for obs in canonical.observations}
        for obs in superseded.observations:
            if obs.observation_id in existing_obs_ids or obs.frame_id in existing_frame_ids:
                continue
            canonical.observations.append(obs.model_copy(update={"target_track_id": canonical.target_id}))
            existing_obs_ids.add(obs.observation_id)
            existing_frame_ids.add(obs.frame_id)

        canonical.observations.sort(key=lambda item: item.created_at)
        if len(canonical.observations) > self.maximum_observations_per_track:
            del canonical.observations[:-self.maximum_observations_per_track]

        canonical.estimate = self.fusion.fuse(
            self.mission_id, canonical.target_id, canonical.observations, self.wgs84_converter, canonical.state
        )
        return canonical

    def estimates(self) -> list[FusedTargetEstimate]:
        return [
            track.estimate
            for track in self.tracks.values()
            if track.estimate is not None
            and track.state is not TargetTrackState.REJECTED
            and track.superseded_by is None
        ]
