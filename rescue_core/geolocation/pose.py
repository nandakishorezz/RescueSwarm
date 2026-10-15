"""Bounded vehicle pose history with linear position and quaternion SLERP."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, field_validator

from .clock_sync import ClockSyncEstimator, ClockSyncQuality
from .transforms import normalize_quaternion, quaternion_slerp


class PoseSample(BaseModel):
    vehicle_id: str
    autopilot_boot_time_ns: int = Field(ge=0)
    host_monotonic_time_ns: int = Field(ge=0)
    host_wall_time_ns: int = Field(ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    absolute_altitude_m: float | None = None
    relative_altitude_m: float | None = None
    local_position_ned_m: tuple[float, float, float] | None = None
    quaternion_body_to_ned: tuple[float, float, float, float]
    roll_rad: float | None = None
    pitch_rad: float | None = None
    yaw_rad: float | None = None
    velocity_ned_m_s: tuple[float, float, float] | None = None
    gps_fix: int | None = Field(default=None, ge=0)
    satellites: int | None = Field(default=None, ge=0)
    horizontal_accuracy_m: float | None = Field(default=None, ge=0)
    vertical_accuracy_m: float | None = Field(default=None, ge=0)
    accuracy_sources: dict[str, str] = Field(default_factory=dict)
    source_message_ids: list[str] = Field(default_factory=list)
    sim_source_time_ns: int | None = Field(default=None, ge=0)
    timestamp_source: str | None = None

    @field_validator("quaternion_body_to_ned")
    @classmethod
    def normalize_attitude(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return tuple(float(component) for component in normalize_quaternion(value))


class PoseLookupDiagnostics(BaseModel):
    frame_host_monotonic_ns: int
    target_autopilot_time_ns: int | None
    pose_before_host_ns: int
    pose_after_host_ns: int
    pose_before_autopilot_ns: int
    pose_after_autopilot_ns: int
    interpolation_fraction: float = Field(ge=0, le=1)
    nearest_sample_age_ms: float = Field(ge=0)
    interpolation_gap_ms: float = Field(ge=0)
    extrapolated: bool = False
    clock_sync_quality: ClockSyncQuality


class InterpolatedPose(BaseModel):
    sample: PoseSample
    diagnostics: PoseLookupDiagnostics


@dataclass
class PoseHistoryStats:
    accepted: int = 0
    rejected_out_of_order: int = 0
    lookup_rejections: int = 0
    maximum_observed_gap_ms: float = 0.0


class PoseHistory:
    def __init__(
        self, history_seconds: float = 10, max_interpolation_gap_ms: float = 250,
        max_extrapolation_ms: float = 50,
    ):
        if history_seconds <= 0 or max_interpolation_gap_ms <= 0 or max_extrapolation_ms < 0:
            raise ValueError("PoseHistory limits are invalid")
        self.history_ns = int(history_seconds * 1e9)
        self.max_gap_ns = int(max_interpolation_gap_ms * 1e6)
        self.max_extrapolation_ns = int(max_extrapolation_ms * 1e6)
        self.samples: list[PoseSample] = []
        self.stats = PoseHistoryStats()

    def add(self, sample: PoseSample) -> None:
        if self.samples and sample.autopilot_boot_time_ns <= self.samples[-1].autopilot_boot_time_ns:
            self.stats.rejected_out_of_order += 1
            return
        if self.samples:
            gap_ms = (sample.autopilot_boot_time_ns - self.samples[-1].autopilot_boot_time_ns) / 1e6
            self.stats.maximum_observed_gap_ms = max(self.stats.maximum_observed_gap_ms, gap_ms)
        self.samples.append(sample)
        cutoff = sample.autopilot_boot_time_ns - self.history_ns
        first_valid = bisect.bisect_left([value.autopilot_boot_time_ns for value in self.samples], cutoff)
        if first_valid:
            del self.samples[:first_valid]
        self.stats.accepted += 1

    def lookup(self, frame_host_monotonic_ns: int, clock_sync: ClockSyncEstimator | None = None) -> InterpolatedPose:
        if not self.samples:
            self.stats.lookup_rejections += 1
            raise ValueError("POSE_UNAVAILABLE: history is empty")
        sync_quality = clock_sync.quality if clock_sync else ClockSyncQuality.UNSYNCED
        if clock_sync and clock_sync.offset_ns is not None:
            target_ns = clock_sync.host_to_autopilot_ns(frame_host_monotonic_ns)
        else:
            # Fallback uses each sample's observed host/autopilot relation and is explicitly UNSYNCED.
            reference = min(self.samples, key=lambda sample: abs(sample.host_monotonic_time_ns - frame_host_monotonic_ns))
            target_ns = reference.autopilot_boot_time_ns + frame_host_monotonic_ns - reference.host_monotonic_time_ns
        times = [sample.autopilot_boot_time_ns for sample in self.samples]
        index = bisect.bisect_left(times, target_ns)
        extrapolated = False
        if index == 0:
            before = after = self.samples[0]
            distance = before.autopilot_boot_time_ns - target_ns
            extrapolated = distance != 0
            if distance > self.max_extrapolation_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("POSE_UNAVAILABLE: frame precedes history beyond extrapolation limit")
            fraction = 0.0
        elif index == len(self.samples):
            before = after = self.samples[-1]
            distance = target_ns - before.autopilot_boot_time_ns
            extrapolated = distance != 0
            if distance > self.max_extrapolation_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("POSE_UNAVAILABLE: frame follows history beyond extrapolation limit")
            fraction = 0.0
        else:
            before, after = self.samples[index - 1], self.samples[index]
            gap = after.autopilot_boot_time_ns - before.autopilot_boot_time_ns
            if gap > self.max_gap_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("POSE_UNAVAILABLE: interpolation gap exceeds configured maximum")
            fraction = (target_ns - before.autopilot_boot_time_ns) / gap
        sample = self._interpolate(before, after, fraction, target_ns, frame_host_monotonic_ns)
        gap_ns = after.autopilot_boot_time_ns - before.autopilot_boot_time_ns
        nearest_age = min(abs(target_ns - before.autopilot_boot_time_ns), abs(after.autopilot_boot_time_ns - target_ns))
        diagnostics = PoseLookupDiagnostics(
            frame_host_monotonic_ns=frame_host_monotonic_ns,
            target_autopilot_time_ns=target_ns,
            pose_before_host_ns=before.host_monotonic_time_ns,
            pose_after_host_ns=after.host_monotonic_time_ns,
            pose_before_autopilot_ns=before.autopilot_boot_time_ns,
            pose_after_autopilot_ns=after.autopilot_boot_time_ns,
            interpolation_fraction=fraction,
            nearest_sample_age_ms=nearest_age / 1e6,
            interpolation_gap_ms=gap_ns / 1e6,
            extrapolated=extrapolated,
            clock_sync_quality=sync_quality,
        )
        return InterpolatedPose(sample=sample, diagnostics=diagnostics)

    def lookup_autopilot_time(self, target_ns: int, *, clock_sync_quality: ClockSyncQuality) -> InterpolatedPose:
        """Strict source-time lookup with no host-receipt fallback."""
        if clock_sync_quality is not ClockSyncQuality.SYNCED:
            self.stats.lookup_rejections += 1
            raise ValueError(f"CLOCK_MAPPING_INVALID: {clock_sync_quality.value}")
        if not self.samples:
            self.stats.lookup_rejections += 1
            raise ValueError("NO_POSE_BRACKET: history is empty")
        times = [sample.autopilot_boot_time_ns for sample in self.samples]
        index = bisect.bisect_left(times, target_ns)
        extrapolated = False
        if index == 0:
            before = after = self.samples[0]
            distance = before.autopilot_boot_time_ns - target_ns
            extrapolated = distance != 0
            if distance > self.max_extrapolation_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("NO_POSE_BRACKET: frame precedes history")
            fraction = 0.0
        elif index == len(self.samples):
            before = after = self.samples[-1]
            distance = target_ns - before.autopilot_boot_time_ns
            extrapolated = distance != 0
            if distance > self.max_extrapolation_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("NO_POSE_BRACKET: frame follows history")
            fraction = 0.0
        else:
            before, after = self.samples[index - 1], self.samples[index]
            gap = after.autopilot_boot_time_ns - before.autopilot_boot_time_ns
            if gap > self.max_gap_ns:
                self.stats.lookup_rejections += 1
                raise ValueError("NO_POSE_BRACKET: interpolation gap exceeds configured maximum")
            fraction = (target_ns - before.autopilot_boot_time_ns) / gap
        gap_ns = after.autopilot_boot_time_ns - before.autopilot_boot_time_ns
        nearest_age = min(abs(target_ns - before.autopilot_boot_time_ns), abs(after.autopilot_boot_time_ns - target_ns))
        sample = self._interpolate(before, after, fraction, target_ns, before.host_monotonic_time_ns)
        return InterpolatedPose(sample=sample, diagnostics=PoseLookupDiagnostics(
            frame_host_monotonic_ns=before.host_monotonic_time_ns,
            target_autopilot_time_ns=target_ns,
            pose_before_host_ns=before.host_monotonic_time_ns,
            pose_after_host_ns=after.host_monotonic_time_ns,
            pose_before_autopilot_ns=before.autopilot_boot_time_ns,
            pose_after_autopilot_ns=after.autopilot_boot_time_ns,
            interpolation_fraction=fraction,
            nearest_sample_age_ms=nearest_age / 1e6,
            interpolation_gap_ms=gap_ns / 1e6,
            extrapolated=extrapolated,
            clock_sync_quality=clock_sync_quality,
        ))

    @staticmethod
    def _optional_linear(first: float | None, second: float | None, fraction: float) -> float | None:
        if first is None or second is None:
            return first if fraction < 0.5 else second
        return first + fraction * (second - first)

    @classmethod
    def _tuple_linear(cls, first: tuple[float, ...] | None, second: tuple[float, ...] | None, fraction: float) -> tuple[float, ...] | None:
        if first is None or second is None:
            return first if fraction < 0.5 else second
        return tuple(cls._optional_linear(a, b, fraction) for a, b in zip(first, second, strict=True))  # type: ignore[arg-type,return-value]

    @classmethod
    def _interpolate(
        cls, before: PoseSample, after: PoseSample, fraction: float,
        target_autopilot_ns: int, target_host_ns: int,
    ) -> PoseSample:
        quaternion = quaternion_slerp(before.quaternion_body_to_ned, after.quaternion_body_to_ned, fraction)
        return PoseSample(
            vehicle_id=before.vehicle_id,
            autopilot_boot_time_ns=target_autopilot_ns,
            host_monotonic_time_ns=target_host_ns,
            host_wall_time_ns=int(before.host_wall_time_ns + fraction * (after.host_wall_time_ns - before.host_wall_time_ns)),
            latitude=cls._optional_linear(before.latitude, after.latitude, fraction),
            longitude=cls._optional_linear(before.longitude, after.longitude, fraction),
            absolute_altitude_m=cls._optional_linear(before.absolute_altitude_m, after.absolute_altitude_m, fraction),
            relative_altitude_m=cls._optional_linear(before.relative_altitude_m, after.relative_altitude_m, fraction),
            local_position_ned_m=cls._tuple_linear(before.local_position_ned_m, after.local_position_ned_m, fraction),
            quaternion_body_to_ned=tuple(float(value) for value in quaternion),
            roll_rad=cls._optional_linear(before.roll_rad, after.roll_rad, fraction),
            pitch_rad=cls._optional_linear(before.pitch_rad, after.pitch_rad, fraction),
            yaw_rad=None,
            velocity_ned_m_s=cls._tuple_linear(before.velocity_ned_m_s, after.velocity_ned_m_s, fraction),
            gps_fix=min(value for value in (before.gps_fix, after.gps_fix) if value is not None) if any(value is not None for value in (before.gps_fix, after.gps_fix)) else None,
            satellites=min(value for value in (before.satellites, after.satellites) if value is not None) if any(value is not None for value in (before.satellites, after.satellites)) else None,
            horizontal_accuracy_m=cls._optional_linear(before.horizontal_accuracy_m, after.horizontal_accuracy_m, fraction),
            vertical_accuracy_m=cls._optional_linear(before.vertical_accuracy_m, after.vertical_accuracy_m, fraction),
            accuracy_sources={**before.accuracy_sources, **after.accuracy_sources},
            source_message_ids=sorted(set(before.source_message_ids + after.source_message_ids)),
            sim_source_time_ns=(
                target_autopilot_ns
                if before.timestamp_source == after.timestamp_source
                == "SITL_SIM_SOURCE_SIDECAR" else None),
            timestamp_source=(
                "SITL_SIM_SOURCE_SIDECAR"
                if before.timestamp_source == after.timestamp_source
                == "SITL_SIM_SOURCE_SIDECAR" else None),
        )
