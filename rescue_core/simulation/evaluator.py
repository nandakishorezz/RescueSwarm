"""Observer-only comparison of Gazebo truth and MAVLink estimates."""

from __future__ import annotations

import math
from pydantic import BaseModel

from rescue_core.geolocation.transforms import normalize_quaternion

from .truth import WorldTruthVehicleState


class AutopilotEstimatedVehicleState(BaseModel):
    vehicle_id: str
    sim_time_s: float
    position_gazebo_m: tuple[float, float, float]
    velocity_gazebo_mps: tuple[float, float, float]
    quaternion_body_to_gazebo: tuple[float, float, float, float]
    source: str = "MAVLINK_ESTIMATE"


class PoseDiscrepancy(BaseModel):
    sim_time_s: float
    position_error_m: float
    velocity_error_mps: float
    attitude_error_deg: float


def compare_pose(truth: WorldTruthVehicleState, estimate: AutopilotEstimatedVehicleState) -> PoseDiscrepancy:
    position = math.sqrt(sum((a - b) ** 2 for a, b in zip(truth.position_gazebo_m, estimate.position_gazebo_m)))
    velocity = math.sqrt(sum((a - b) ** 2 for a, b in zip(truth.velocity_gazebo_mps, estimate.velocity_gazebo_mps)))
    q0 = normalize_quaternion(truth.quaternion_body_to_gazebo)
    q1 = normalize_quaternion(estimate.quaternion_body_to_gazebo)
    dot = min(1.0, abs(float(q0 @ q1)))
    attitude = math.degrees(2 * math.acos(dot))
    return PoseDiscrepancy(sim_time_s=truth.sim_time_s, position_error_m=position, velocity_error_mps=velocity, attitude_error_deg=attitude)
