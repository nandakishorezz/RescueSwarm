from __future__ import annotations

import asyncio

from ground.backend.app.services.geolocation_service import GeolocationMissionService
from tools.stage5_synthetic_validation import run_synthetic_validation


class Repository:
    def __init__(self):
        self.values = []

    def upsert(self, value):
        self.values.append(value)

    def add(self, value):
        self.values.append(value)

    def get(self, target_id):
        return next((item for item in reversed(self.values) if getattr(item, "target_id", None) == target_id), None)


class Websocket:
    def __init__(self):
        self.envelopes = []

    async def broadcast(self, envelope):
        self.envelopes.append(envelope)


def test_geolocation_service_persists_and_broadcasts_normalized_target() -> None:
    result = run_synthetic_validation(seed=1, monte_carlo_samples=10)
    targets, events, websocket = Repository(), Repository(), Websocket()
    service = GeolocationMissionService(targets, events, websocket)
    asyncio.run(service.publish_observation("stage5-synthetic", result["observations"][0]))
    target = asyncio.run(service.publish_target(result["targets"][0]))
    assert target.state.value == "CONFIRMED"
    assert target.geolocation_uncertainty_m > 0
    assert target.detection_confidence == 0.9
    assert targets.values == [target]
    assert [envelope.type for envelope in websocket.envelopes] == ["target.observation", "target.confirmed"]
    observation_payload = websocket.envelopes[0].payload
    assert observation_payload["frame_id"] == result["observations"][0].frame_id
    assert observation_payload["pixel_strategy"] == result["observations"][0].pixel_strategy
    assert len(observation_payload["camera_ray"]) == 3
    assert len(observation_payload["world_ray_enu"]) == 3
    assert len(observation_payload["target_enu_m"]) == 3


def test_superseded_target_cannot_be_reconfirmed_by_delayed_callback() -> None:
    result = run_synthetic_validation(seed=1, monte_carlo_samples=10)
    targets, events, websocket = Repository(), Repository(), Websocket()
    service = GeolocationMissionService(targets, events, websocket)
    estimate = result["targets"][0]
    asyncio.run(service.publish_target(estimate))
    asyncio.run(service.publish_target_superseded(estimate.target_id, "canonical-id"))
    before_events = len(websocket.envelopes)
    delayed = asyncio.run(service.publish_target(estimate))
    assert delayed.state.value == "REJECTED"
    assert len(websocket.envelopes) == before_events
