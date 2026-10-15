"""Validated flight parameters model for autonomous search and delivery missions."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class FlightParameters(BaseModel):
    """Single typed source of truth for flight setpoints and delivery configuration."""

    search_altitude_m: float = Field(
        default=15.0,
        ge=3.0,
        le=50.0,
        description="Search cruise altitude above ground level in meters (3.0 - 50.0m)",
    )
    search_speed_m_s: float = Field(
        default=4.0,
        ge=1.0,
        le=15.0,
        description="Search cruise speed in m/s (1.0 - 15.0 m/s)",
    )
    transit_speed_m_s: float = Field(
        default=5.0,
        ge=1.0,
        le=15.0,
        description="Transit speed between search tracks in m/s (1.0 - 15.0 m/s)",
    )
    approach_altitude_m: float = Field(
        default=8.0,
        ge=3.0,
        le=30.0,
        description="Survivor-approach altitude in meters (3.0 - 30.0m)",
    )
    approach_speed_m_s: float = Field(
        default=3.0,
        ge=0.5,
        le=8.0,
        description="Survivor-approach navigation speed in m/s (0.5 - 8.0 m/s)",
    )
    delivery_altitude_m: float = Field(
        default=4.0,
        ge=1.0,
        le=10.0,
        description="Delivery hover altitude in meters (1.0 - 10.0m)",
    )
    delivery_stabilization_s: float = Field(
        default=3.0,
        ge=0.5,
        le=15.0,
        description="Delivery stabilization/hold duration before release in seconds (0.5 - 15.0s)",
    )
    safe_drop_offset_m: float = Field(
        default=2.5,
        ge=1.0,
        le=10.0,
        description="Safe payload-drop offset from survivor in meters (1.0 - 10.0m; never 0)",
    )
    search_swath_overlap_percent: float = Field(
        default=50.0,
        ge=10.0,
        le=90.0,
        description="Search-swath overlap percentage (10.0 - 90.0%)",
    )
    rtl_altitude_m: float = Field(
        default=15.0,
        ge=3.0,
        le=50.0,
        description="Return-to-Launch altitude in meters (3.0 - 50.0m)",
    )
    rtl_speed_m_s: float = Field(
        default=4.0,
        ge=1.0,
        le=15.0,
        description="Return-to-Launch return speed in m/s (1.0 - 15.0 m/s)",
    )
    payload_slots: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Number of simulated payload kits carried (1 - 10)",
    )

    @model_validator(mode="after")
    def validate_flight_envelope(self) -> "FlightParameters":
        if self.delivery_altitude_m >= self.approach_altitude_m:
            raise ValueError(
                f"Delivery altitude ({self.delivery_altitude_m:.1f}m) must be strictly below "
                f"approach altitude ({self.approach_altitude_m:.1f}m)"
            )
        if self.delivery_altitude_m >= self.search_altitude_m:
            raise ValueError(
                f"Delivery altitude ({self.delivery_altitude_m:.1f}m) must be strictly below "
                f"search altitude ({self.search_altitude_m:.1f}m)"
            )
        if self.safe_drop_offset_m < 1.0:
            raise ValueError(
                f"Safe drop offset ({self.safe_drop_offset_m:.1f}m) must be at least 1.0m to avoid dropping directly on a person"
            )
        return self
