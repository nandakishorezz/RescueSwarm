"""Hardware capability declarations separated from physical verification."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .models import EvidenceStatus


class CapabilityState(BaseModel):
    """Configured capability plus independently recorded evidence status."""

    configured: Any = None
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    detected_value: Any = None
    evidence: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "CapabilityState":
        if self.status is EvidenceStatus.VERIFIED and not self.evidence:
            raise ValueError("VERIFIED capabilities require evidence")
        return self


class VehicleCapabilities(BaseModel):
    """Replaceable-electronics capability map for one vehicle."""

    autopilot: CapabilityState
    flight_controller: CapabilityState
    gps: CapabilityState
    precision_gnss: CapabilityState
    camera: CapabilityState
    global_shutter: CapabilityState
    rangefinder: CapabilityState
    edge_compute: CapabilityState
    ai_accelerator: CapabilityState
    payload: CapabilityState
    payload_feedback: CapabilityState
    network: CapabilityState
    fleet_size: int = Field(ge=1)

    @classmethod
    def from_prototype_config(cls, config: dict[str, Any]) -> "VehicleCapabilities":
        """Build configured (not verified) capabilities from prototype YAML."""

        configured = EvidenceStatus.CONFIGURED
        camera = config["camera"]
        autopilot = config["autopilot"]
        gps = config["gps"]
        edge = config["edge"]
        payload = config.get("payload", {})
        return cls(
            autopilot=CapabilityState(configured=autopilot["type"], status=configured),
            flight_controller=CapabilityState(
                configured=autopilot["flight_controller"], status=configured
            ),
            gps=CapabilityState(configured=gps["type"], status=configured),
            precision_gnss=CapabilityState(
                configured=gps.get("precision_gnss", False), status=configured
            ),
            camera=CapabilityState(configured=camera["type"], status=configured),
            global_shutter=CapabilityState(
                configured=camera.get("global_shutter", False), status=configured
            ),
            rangefinder=CapabilityState(
                configured=config.get("rangefinder", {}).get("present", False), status=configured
            ),
            edge_compute=CapabilityState(configured=edge["type"], status=configured),
            ai_accelerator=CapabilityState(
                configured=edge.get("ai_accelerator", "none"), status=configured
            ),
            payload=CapabilityState(
                configured=payload.get("type", "unknown"), status=EvidenceStatus.UNKNOWN
            ),
            payload_feedback=CapabilityState(
                configured=payload.get("feedback", "unknown"), status=EvidenceStatus.UNKNOWN
            ),
            network=CapabilityState(
                configured={
                    "external_internet_required": config["network"][
                        "external_internet_required"
                    ]
                },
                status=configured,
            ),
            fleet_size=config["vehicle"]["fleet_size"],
        )

