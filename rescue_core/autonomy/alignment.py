"""Bounded proportional close-range alignment and drop gating."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .models import ReacquisitionObservation


@dataclass(frozen=True)
class AlignmentCommand:
    velocity_east_m_s: float
    velocity_north_m_s: float
    horizontal_error_m: float
    aligned: bool


class PrecisionAlignmentController:
    def __init__(
        self, kp: float, max_horizontal_speed_m_s: float, deadband_m: float,
        alignment_radius_m: float, observation_stale_s: float, kd: float = 0.0,
        damping_range_m: float = 2.0,
    ):
        if min(kp, max_horizontal_speed_m_s, alignment_radius_m, observation_stale_s) <= 0 or deadband_m < 0 or kd < 0 or damping_range_m <= 0:
            raise ValueError("Invalid alignment controller configuration")
        self.kp = kp
        self.kd = kd
        self.damping_range_m = damping_range_m
        self.max_speed = max_horizontal_speed_m_s
        self.deadband = deadband_m
        self.alignment_radius = alignment_radius_m
        self.stale_ns = int(observation_stale_s * 1e9)

    def command(
        self, observation: ReacquisitionObservation, now_ns: int | None = None,
        vehicle_velocity_east_m_s: float = 0.0, vehicle_velocity_north_m_s: float = 0.0,
    ) -> AlignmentCommand:
        now = now_ns if now_ns is not None else time.monotonic_ns()
        if now - observation.timestamp_monotonic_ns > self.stale_ns:
            raise TimeoutError("Reacquisition observation is stale")
        if not observation.visible or observation.relative_east_m is None or observation.relative_north_m is None:
            raise ValueError("Target is not visible")
        east, north = observation.relative_east_m, observation.relative_north_m
        error = math.hypot(east, north)

        aligned = error <= self.alignment_radius
        if error <= 1e-9:
            # A zero velocity setpoint asks ArduPilot's velocity loop to stop;
            # negating measured velocity here created full-speed reversals.
            cmd_e = cmd_n = 0.0
        else:
            unit_e, unit_n = east / error, north / error
            radial_velocity = (
                vehicle_velocity_east_m_s * unit_e
                + vehicle_velocity_north_m_s * unit_n
            )
            lateral_e = vehicle_velocity_east_m_s - radial_velocity * unit_e
            lateral_n = vehicle_velocity_north_m_s - radial_velocity * unit_n

            # Monotone approach envelope.  The internal settling target is
            # the controller deadband, not the outer acceptance radius; using
            # the latter made the requested speed asymptote to zero at the
            # qualification boundary instead of carrying the vehicle inside.
            braking_acceleration = max(0.1, self.kd * self.max_speed)
            braking_distance = max(error - self.deadband, 0.0)
            allowed_approach_speed = min(
                self.max_speed,
                self.kp * error,
                math.sqrt(2.0 * braking_acceleration * braking_distance),
            )
            if error <= self.deadband:
                requested_radial_speed = 0.0
            elif radial_velocity > allowed_approach_speed:
                requested_radial_speed = (
                    allowed_approach_speed * allowed_approach_speed
                    / max(radial_velocity, 1e-9)
                )
            else:
                requested_radial_speed = allowed_approach_speed

            # Only cross-track drift is actively opposed.  This term cannot
            # reverse the target-axis request and remains globally bounded.
            cmd_e = requested_radial_speed * unit_e - self.kd * lateral_e
            cmd_n = requested_radial_speed * unit_n - self.kd * lateral_n

        cmd_speed = math.hypot(cmd_e, cmd_n)
        if cmd_speed > self.max_speed:
            cmd_e = (cmd_e / cmd_speed) * self.max_speed
            cmd_n = (cmd_n / cmd_speed) * self.max_speed

        return AlignmentCommand(cmd_e, cmd_n, error, aligned)


    def drop_ready(
        self, observation: ReacquisitionObservation, *, altitude_m: float,
        delivery_altitude_m: float, altitude_tolerance_m: float,
        horizontal_speed_m_s: float, max_horizontal_speed_m_s: float,
    ) -> bool:
        # Note: drop_ready in tests might not pass velocity, but it doesn't matter for the boolean returned
        command = self.command(observation)
        return (
            command.horizontal_error_m <= self.alignment_radius
            and abs(altitude_m - delivery_altitude_m) <= altitude_tolerance_m
            and horizontal_speed_m_s <= max_horizontal_speed_m_s
        )
