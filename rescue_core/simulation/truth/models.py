"""DTOs which are intentionally incompatible with autonomy estimated-state DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorldTruthVehicleState(BaseModel):
    simulation_entity: str
    sim_time_s: float = Field(ge=0)
    wall_time: str
    position_gazebo_m: tuple[float, float, float]
    velocity_gazebo_mps: tuple[float, float, float]
    quaternion_body_to_gazebo: tuple[float, float, float, float]
    angular_rate_body_rad_s: tuple[float, float, float]
    wind_gazebo_mps: tuple[float, float, float]


class WorldTruthSurvivor(BaseModel):
    simulation_entity: str
    survivor_id: str
    position_gazebo_m: tuple[float, float, float]
    position_enu_m: tuple[float, float, float]
    position_wgs84: tuple[float, float, float]
    body_state: str
    enabled: bool
    truth_notice: str = "SIMULATION_ONLY_GROUND_TRUTH"


class SimulationGroundTruth(BaseModel):
    run_id: str
    simulation_fingerprint: str
    survivors: list[WorldTruthSurvivor]
    latest_vehicle_state: WorldTruthVehicleState | None = None
