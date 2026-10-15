"""Authoritative shared survivor registry for multi-drone operations.

Every confirmed survivor has exactly one fleet-wide canonical ID, independent of
which drone observed it. Raw or single-frame detections remain internal candidates
until temporally confirmed. Cross-drone observations are associated into the canonical
record using uncertainty-aware spatial gating and co-occurrence deduplication.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field

from .contracts import AssignmentState, PrioritySource, ServicePriority, SharedSurvivorRecord, SurvivorObservation


class _InternalObservation(BaseModel):
    observation_id: str
    vehicle_id: str
    frame_id: str
    observed_at: datetime
    confidence: float
    latitude: float | None = None
    longitude: float | None = None
    east_m: float | None = None
    north_m: float | None = None
    uncertainty_m: float | None = None
    track_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    evidence_reference: str | None = None


class _InternalCandidate:
    def __init__(self, candidate_id: str, first_obs: _InternalObservation):
        self.candidate_id = candidate_id
        self.observations: list[_InternalObservation] = [first_obs]
        self.first_seen_at = first_obs.observed_at
        self.last_seen_at = first_obs.observed_at

    def add(self, obs: _InternalObservation) -> None:
        self.observations.append(obs)
        if obs.observed_at < self.first_seen_at:
            self.first_seen_at = obs.observed_at
        if obs.observed_at > self.last_seen_at:
            self.last_seen_at = obs.observed_at

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def distinct_frames(self) -> int:
        return len({(obs.vehicle_id, obs.frame_id) for obs in self.observations})

    @property
    def time_span_s(self) -> float:
        return abs((self.last_seen_at - self.first_seen_at).total_seconds())

    @property
    def source_drone_ids(self) -> list[str]:
        return sorted(list({obs.vehicle_id for obs in self.observations}))

    @property
    def source_track_ids(self) -> list[str]:
        return sorted(list({obs.track_id for obs in self.observations if obs.track_id}))


class SharedSurvivorRegistry:
    """Thread-safe authoritative fleet-wide survivor registry.
    
    Maintains internal candidate tracks and authoritative confirmed survivor records.
    Only confirmed survivors are exposed to coordinator and fleet status.
    """

    def __init__(
        self,
        session_id: UUID,
        mission_id: str,
        *,
        association_radius_m: float = 6.0,
        association_sigma: float = 2.5,
        uncertainty_floor_m: float = 0.75,
        minimum_observations: int = 3,
        minimum_time_span_s: float = 0.15,
        on_confirmed_callback: Callable[[SharedSurvivorRecord], None] | None = None,
        on_event_callback: Callable[[str | None, str, str, str, dict | None], None] | None = None,
    ):
        if association_radius_m <= 0 or uncertainty_floor_m <= 0:
            raise ValueError("Association thresholds must be positive")
        self.session_id = session_id
        self.mission_id = mission_id
        self.association_radius_m = association_radius_m
        self.association_sigma = association_sigma
        self.uncertainty_floor_m = uncertainty_floor_m
        self.minimum_observations = minimum_observations
        self.minimum_time_span_s = minimum_time_span_s
        self.on_confirmed_callback = on_confirmed_callback
        self.on_event_callback = on_event_callback

        self._lock = threading.RLock()
        self._confirmed: dict[str, SharedSurvivorRecord] = {}
        self._candidates: dict[str, _InternalCandidate] = {}
        self._observation_history: dict[str, list[_InternalObservation]] = {}  # canonical_id -> obs
        self._seen_observation_ids: set[str] = set()
        self._target_sequence = 0

    def reset(self) -> None:
        with self._lock:
            self._confirmed.clear()
            self._candidates.clear()
            self._observation_history.clear()
            self._seen_observation_ids.clear()
            self._target_sequence = 0

    def confirmed_survivors(self) -> list[SharedSurvivorRecord]:
        with self._lock:
            return [record.model_copy(deep=True) for record in self._confirmed.values()]

    def get_survivor(self, canonical_id: str) -> SharedSurvivorRecord | None:
        with self._lock:
            record = self._confirmed.get(canonical_id)
            return record.model_copy(deep=True) if record else None

    def update_assignment(
        self,
        canonical_id: str,
        assignment_id: str | None,
        servicer_id: str | None,
        assignment_state: AssignmentState | str | None,
    ) -> SharedSurvivorRecord | None:
        with self._lock:
            record = self._confirmed.get(canonical_id)
            if record is None:
                return None
            record.assignment_id = assignment_id
            record.assigned_servicer_id = servicer_id
            record.assignment_state = assignment_state
            return record.model_copy(deep=True)

    def update_priority(
        self, canonical_id: str, priority: ServicePriority, source: PrioritySource,
    ) -> SharedSurvivorRecord:
        """Change evaluation priority without modifying detection or target identity."""
        with self._lock:
            record = self._confirmed.get(canonical_id)
            if record is None:
                raise ValueError(f"Confirmed survivor {canonical_id} not found")
            if source == PrioritySource.DEFAULT:
                raise ValueError("Explicit priority changes require OPERATOR or MISSION provenance")
            record.priority = ServicePriority(priority)
            record.priority_source = PrioritySource(source)
            record.priority_updated_at = datetime.now(UTC)
            return record.model_copy(deep=True)

    def mark_delivered(self, canonical_id: str) -> SharedSurvivorRecord:
        with self._lock:
            record = self._confirmed.get(canonical_id)
            if record is None:
                raise ValueError(f"Confirmed survivor {canonical_id} not found")
            record.delivery_status = "DELIVERED"
            return record.model_copy(deep=True)

    def ingest(
        self,
        vehicle_id: str,
        observation: Any,
        estimate: Any = None,
    ) -> SharedSurvivorRecord | None:
        """Ingest an observation from any drone.
        
        Returns the confirmed SharedSurvivorRecord if confirmed, or None if candidate/duplicate.
        """
        internal_obs, is_confirmed_upstream = self._normalize_observation(vehicle_id, observation, estimate)
        if internal_obs is None:
            return None

        with self._lock:
            # 1. Idempotency: skip if observation_id already processed
            if internal_obs.observation_id in self._seen_observation_ids:
                # Return the existing confirmed record if this observation was already part of one
                for can_id, obs_list in self._observation_history.items():
                    if any(o.observation_id == internal_obs.observation_id for o in obs_list):
                        return self._confirmed[can_id].model_copy(deep=True)
                return None

            self._seen_observation_ids.add(internal_obs.observation_id)

            # 2. Match against existing confirmed records first
            matched_confirmed_id = self._match_confirmed(internal_obs)
            if matched_confirmed_id is not None:
                record = self._confirmed[matched_confirmed_id]
                new_drone = internal_obs.vehicle_id not in record.source_drone_ids
                self._add_obs_to_confirmed(record, internal_obs)
                if new_drone and self.on_event_callback:
                    self.on_event_callback(
                        internal_obs.vehicle_id,
                        "CROSS_DRONE_ASSOCIATION",
                        f"Survivor {record.canonical_id} cross-associated with observations from {internal_obs.vehicle_id}",
                        "INFO",
                        {"canonical_id": record.canonical_id, "source_drones": record.source_drone_ids},
                    )
                return record.model_copy(deep=True)

            # 3. Match against internal candidates
            matched_candidate_id = self._match_candidate(internal_obs)
            if matched_candidate_id is not None:
                cand = self._candidates[matched_candidate_id]
                # Check for same-frame duplicate in candidate
                if not self._is_same_frame_duplicate(cand.observations, internal_obs):
                    cand.add(internal_obs)

                # Check temporal confirmation (either upstream confirmation or registry criteria)
                should_promote = is_confirmed_upstream or (
                    cand.observation_count >= self.minimum_observations
                    and cand.distinct_frames >= self.minimum_observations
                    and cand.time_span_s >= self.minimum_time_span_s
                )
                if should_promote:
                    # Promote candidate to confirmed!
                    canonical_record = self._promote_candidate(cand)
                    del self._candidates[matched_candidate_id]
                    return canonical_record.model_copy(deep=True)

                return None

            # 4. If observation is pre-confirmed by upstream estimator or marked confirmed and no prior candidate existed
            if is_confirmed_upstream:
                canonical_record = self._create_confirmed_from_observation(internal_obs)
                return canonical_record.model_copy(deep=True)

            # 5. Create new internal candidate
            cand_id = f"cand_{uuid4()}"
            self._candidates[cand_id] = _InternalCandidate(cand_id, internal_obs)
            return None

    def _normalize_observation(
        self,
        vehicle_id: str,
        observation: Any,
        estimate: Any,
    ) -> tuple[_InternalObservation | None, bool]:
        if observation is None:
            return None, False

        obs_id = getattr(observation, "observation_id", None)
        frame_id = getattr(observation, "frame_id", "")
        track_id = getattr(observation, "target_track_id", getattr(observation, "track_id", None))
        confidence = float(getattr(observation, "confidence", getattr(observation, "detection_confidence", 0.0)))
        
        # Timestamps
        observed_at = getattr(observation, "observed_at", getattr(observation, "timestamp", getattr(observation, "created_at", None)))
        if observed_at is None:
            observed_at = datetime.now(UTC)
        elif isinstance(observed_at, str):
            try:
                observed_at = datetime.fromisoformat(observed_at)
            except ValueError:
                observed_at = datetime.now(UTC)

        # Geolocation
        lat = getattr(observation, "latitude", getattr(observation, "target_latitude", None))
        lon = getattr(observation, "longitude", getattr(observation, "target_longitude", None))
        east = getattr(observation, "east_m", getattr(observation, "target_east_m", None))
        north = getattr(observation, "north_m", getattr(observation, "target_north_m", None))
        unc = getattr(observation, "uncertainty_m", getattr(observation, "position_uncertainty_m", None))

        bbox = getattr(observation, "bbox_original_px", getattr(observation, "bbox_xywh", None))
        if bbox is not None:
            try:
                bbox = tuple(float(x) for x in bbox)
            except (TypeError, ValueError):
                bbox = None

        evidence_ref = None
        evidence_refs = getattr(observation, "evidence_references", None)
        if evidence_refs and len(evidence_refs) > 0:
            evidence_ref = evidence_refs[0]

        is_confirmed = False
        if getattr(observation, "is_confirmed", False):
            is_confirmed = True
        if estimate is not None:
            st = getattr(estimate, "state", None)
            st_val = getattr(st, "value", str(st))
            if st_val == "CONFIRMED":
                is_confirmed = True
            # Overwrite / supplement coordinates if estimate has better data
            if east is None and getattr(estimate, "east_m", None) is not None:
                east = estimate.east_m
            if north is None and getattr(estimate, "north_m", None) is not None:
                north = estimate.north_m
            if lat is None and getattr(estimate, "latitude", None) is not None:
                lat = estimate.latitude
            if lon is None and getattr(estimate, "longitude", None) is not None:
                lon = estimate.longitude
            if unc is None and getattr(estimate, "uncertainty_m", None) is not None:
                unc = estimate.uncertainty_m

        if not obs_id:
            # Deterministic ID for frame + vehicle + track
            obs_id = str(uuid5(NAMESPACE_URL, f"{vehicle_id}:{frame_id}:{track_id or 'none'}:{observed_at.isoformat()}"))

        return _InternalObservation(
            observation_id=str(obs_id),
            vehicle_id=vehicle_id,
            frame_id=str(frame_id),
            observed_at=observed_at,
            confidence=confidence,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            east_m=float(east) if east is not None else None,
            north_m=float(north) if north is not None else None,
            uncertainty_m=float(unc) if unc is not None else None,
            track_id=str(track_id) if track_id else None,
            bbox=bbox,
            evidence_reference=evidence_ref,
        ), is_confirmed

    def _is_same_frame_duplicate(
        self,
        existing_obs: list[_InternalObservation],
        new_obs: _InternalObservation,
    ) -> bool:
        for obs in existing_obs:
            if obs.vehicle_id == new_obs.vehicle_id and obs.frame_id == new_obs.frame_id:
                if obs.bbox and new_obs.bbox:
                    # Check bounding box IoU
                    left = max(obs.bbox[0], new_obs.bbox[0])
                    top = max(obs.bbox[1], new_obs.bbox[1])
                    right = min(obs.bbox[2], new_obs.bbox[2])
                    bottom = min(obs.bbox[3], new_obs.bbox[3])
                    intersection = max(0.0, right - left) * max(0.0, bottom - top)
                    area1 = max(0.0, obs.bbox[2] - obs.bbox[0]) * max(0.0, obs.bbox[3] - obs.bbox[1])
                    area2 = max(0.0, new_obs.bbox[2] - new_obs.bbox[0]) * max(0.0, new_obs.bbox[3] - new_obs.bbox[1])
                    union = area1 + area2 - intersection
                    iou = intersection / union if union > 0 else 0.0
                    if iou >= 0.35:
                        return True
                else:
                    return True
        return False

    def _match_confirmed(self, obs: _InternalObservation) -> str | None:
        best_match: str | None = None
        best_score = float("inf")

        for canonical_id, record in self._confirmed.items():
            # Check co-occurrence conflict in the same camera frame
            history = self._observation_history.get(canonical_id, [])
            cooccurring = any(
                h.vehicle_id == obs.vehicle_id and h.frame_id == obs.frame_id
                for h in history
            )
            if cooccurring:
                # Two distinct observations in the same frame from the same vehicle cannot be the same survivor
                continue

            # Spatial match
            if obs.east_m is not None and obs.north_m is not None and record.east_m is not None and record.north_m is not None:
                dist = math.hypot(obs.east_m - record.east_m, obs.north_m - record.north_m)
                unc_ref = record.uncertainty_m or self.uncertainty_floor_m
                unc_obs = obs.uncertainty_m or self.uncertainty_floor_m
                combined_unc = math.hypot(unc_ref, unc_obs)
                gate = min(self.association_radius_m, max(self.association_sigma * combined_unc, 2.0))
                if dist <= gate:
                    score = dist / gate
                    if score < best_score:
                        best_score = score
                        best_match = canonical_id
            elif obs.latitude is not None and obs.longitude is not None and record.latitude is not None and record.longitude is not None:
                # Approximate 1 degree ~ 111,000m
                dlat = (obs.latitude - record.latitude) * 111_000.0
                dlon = (obs.longitude - record.longitude) * 111_000.0 * math.cos(math.radians(record.latitude))
                dist = math.hypot(dlat, dlon)
                unc_ref = record.uncertainty_m or self.uncertainty_floor_m
                unc_obs = obs.uncertainty_m or self.uncertainty_floor_m
                combined_unc = math.hypot(unc_ref, unc_obs)
                gate = min(self.association_radius_m, max(self.association_sigma * combined_unc, 2.0))
                if dist <= gate:
                    score = dist / gate
                    if score < best_score:
                        best_score = score
                        best_match = canonical_id

        return best_match

    def _match_candidate(self, obs: _InternalObservation) -> str | None:
        best_match: str | None = None
        best_score = float("inf")

        for cand_id, cand in self._candidates.items():
            # Check co-occurrence conflict in the same camera frame
            cooccurring = any(
                h.vehicle_id == obs.vehicle_id and h.frame_id == obs.frame_id
                for h in cand.observations
            )
            if cooccurring:
                continue

            last_obs = cand.observations[-1]
            if obs.east_m is not None and obs.north_m is not None and last_obs.east_m is not None and last_obs.north_m is not None:
                dist = math.hypot(obs.east_m - last_obs.east_m, obs.north_m - last_obs.north_m)
                unc_ref = last_obs.uncertainty_m or self.uncertainty_floor_m
                unc_obs = obs.uncertainty_m or self.uncertainty_floor_m
                combined_unc = math.hypot(unc_ref, unc_obs)
                gate = min(self.association_radius_m, max(self.association_sigma * combined_unc, 2.0))
                if dist <= gate:
                    score = dist / gate
                    if score < best_score:
                        best_score = score
                        best_match = cand_id
            elif obs.latitude is not None and obs.longitude is not None and last_obs.latitude is not None and last_obs.longitude is not None:
                dlat = (obs.latitude - last_obs.latitude) * 111_000.0
                dlon = (obs.longitude - last_obs.longitude) * 111_000.0 * math.cos(math.radians(last_obs.latitude))
                dist = math.hypot(dlat, dlon)
                unc_ref = last_obs.uncertainty_m or self.uncertainty_floor_m
                unc_obs = obs.uncertainty_m or self.uncertainty_floor_m
                combined_unc = math.hypot(unc_ref, unc_obs)
                gate = min(self.association_radius_m, max(self.association_sigma * combined_unc, 2.0))
                if dist <= gate:
                    score = dist / gate
                    if score < best_score:
                        best_score = score
                        best_match = cand_id

        return best_match

    def _add_obs_to_confirmed(self, record: SharedSurvivorRecord, obs: _InternalObservation) -> None:
        history = self._observation_history.setdefault(record.canonical_id, [])
        history.append(obs)
        record.last_received_at = datetime.now(UTC)
        record.observation_count = len(history)
        if obs.vehicle_id not in record.source_drone_ids:
            record.source_drone_ids = sorted(record.source_drone_ids + [obs.vehicle_id])
        if obs.track_id and obs.track_id not in record.source_track_ids:
            record.source_track_ids = sorted(record.source_track_ids + [obs.track_id])

        if obs.observed_at > record.last_seen_at:
            record.last_seen_at = obs.observed_at
        if obs.observed_at < record.first_seen_at:
            record.first_seen_at = obs.observed_at

        if obs.evidence_reference and obs.evidence_reference not in record.evidence_references:
            record.evidence_references.append(obs.evidence_reference)

        # Refit position and uncertainty using inverse-variance weights
        self._refit_position(record, history)

    def _refit_position(self, record: SharedSurvivorRecord, history: list[_InternalObservation]) -> None:
        pos_samples = [
            (obs.east_m, obs.north_m, obs.latitude, obs.longitude, max(obs.uncertainty_m or self.uncertainty_floor_m, self.uncertainty_floor_m))
            for obs in history
            if obs.east_m is not None and obs.north_m is not None
        ]
        if pos_samples:
            weights = [1.0 / (unc ** 2) for _, _, _, _, unc in pos_samples]
            tot_w = sum(weights)
            record.east_m = sum(e * w for (e, _, _, _, _), w in zip(pos_samples, weights, strict=True)) / tot_w
            record.north_m = sum(n * w for (_, n, _, _, _), w in zip(pos_samples, weights, strict=True)) / tot_w
            lats = [lat for _, _, lat, _, _ in pos_samples if lat is not None]
            lons = [lon for _, _, _, lon, _ in pos_samples if lon is not None]
            if len(lats) == len(pos_samples):
                record.latitude = sum(lat * w for (_, _, lat, _, _), w in zip(pos_samples, weights, strict=True)) / tot_w
            if len(lons) == len(pos_samples):
                record.longitude = sum(lon * w for (_, _, _, lon, _), w in zip(pos_samples, weights, strict=True)) / tot_w
            record.uncertainty_m = max(
                self.uncertainty_floor_m,
                min(unc for _, _, _, _, unc in pos_samples) / math.sqrt(len(pos_samples)),
            )

        conf_samples = [obs.confidence for obs in history if obs.confidence > 0]
        if conf_samples:
            record.confidence = max(conf_samples)
        record.inlier_count = len(history)

    def _create_confirmed_from_observation(self, obs: _InternalObservation) -> SharedSurvivorRecord:
        self._target_sequence += 1
        canonical_id = f"survivor-{self._target_sequence:03d}"
        record = SharedSurvivorRecord(
            canonical_id=canonical_id,
            session_id=self.session_id,
            confirmation_status="CONFIRMED",
            observation_count=1,
            source_drone_ids=[obs.vehicle_id],
            source_track_ids=[obs.track_id] if obs.track_id else [],
            first_seen_at=obs.observed_at,
            last_seen_at=obs.observed_at,
            latitude=obs.latitude,
            longitude=obs.longitude,
            east_m=obs.east_m,
            north_m=obs.north_m,
            uncertainty_m=obs.uncertainty_m or self.uncertainty_floor_m,
            confidence=obs.confidence,
            inlier_count=1,
            evidence_references=[obs.evidence_reference] if obs.evidence_reference else [],
        )
        self._confirmed[canonical_id] = record
        self._observation_history[canonical_id] = [obs]

        if self.on_event_callback:
            self.on_event_callback(
                obs.vehicle_id,
                "SURVIVOR_CONFIRMED",
                f"Survivor {canonical_id} confirmed by {obs.vehicle_id}",
                "INFO",
                {"canonical_id": canonical_id, "confidence": obs.confidence},
            )
        if self.on_confirmed_callback:
            self.on_confirmed_callback(record)

        return record

    def _promote_candidate(self, cand: _InternalCandidate) -> SharedSurvivorRecord:
        self._target_sequence += 1
        canonical_id = f"survivor-{self._target_sequence:03d}"
        first_obs = cand.observations[0]
        record = SharedSurvivorRecord(
            canonical_id=canonical_id,
            session_id=self.session_id,
            confirmation_status="CONFIRMED",
            observation_count=cand.observation_count,
            source_drone_ids=cand.source_drone_ids,
            source_track_ids=cand.source_track_ids,
            first_seen_at=cand.first_seen_at,
            last_seen_at=cand.last_seen_at,
            latitude=first_obs.latitude,
            longitude=first_obs.longitude,
            east_m=first_obs.east_m,
            north_m=first_obs.north_m,
            uncertainty_m=first_obs.uncertainty_m or self.uncertainty_floor_m,
            confidence=max(obs.confidence for obs in cand.observations),
            inlier_count=cand.observation_count,
            evidence_references=[
                obs.evidence_reference for obs in cand.observations if obs.evidence_reference
            ],
        )
        self._refit_position(record, cand.observations)
        self._confirmed[canonical_id] = record
        self._observation_history[canonical_id] = list(cand.observations)

        if self.on_event_callback:
            self.on_event_callback(
                cand.source_drone_ids[0] if cand.source_drone_ids else None,
                "SURVIVOR_CONFIRMED",
                f"Survivor {canonical_id} temporally confirmed across {cand.distinct_frames} frames",
                "INFO",
                {"canonical_id": canonical_id, "frames": cand.distinct_frames, "time_span_s": round(cand.time_span_s, 2)},
            )
        if self.on_confirmed_callback:
            self.on_confirmed_callback(record)

        return record
