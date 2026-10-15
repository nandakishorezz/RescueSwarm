from fastapi.testclient import TestClient

from ground.backend.app.domain.mission import Mission
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.simulation.drone_simulator import SimulatedVehicleSource


def test_simulator_is_deterministic(valid_geojson: bytes) -> None:
    mission = Mission(name="Simulation")
    mission.boundary = BoundaryService(10).import_boundary("area.geojson", valid_geojson)
    first = SimulatedVehicleSource(mission, seed=7, total_steps=12)
    second = SimulatedVehicleSource(mission, seed=7, total_steps=12)
    first_updates = [first.next_update() for _ in range(12)]
    second_updates = [second.next_update() for _ in range(12)]
    assert [item.vehicle.latitude for item in first_updates if item] == [
        item.vehicle.latitude for item in second_updates if item
    ]
    assert first.target is not None and second.target is not None
    assert first.target.detection_confidence == second.target.detection_confidence
    assert first_updates[-1].mission_state.value == "COMPLETED"


def test_simulator_generates_target_lifecycle(valid_geojson: bytes) -> None:
    mission = Mission(name="Targets")
    mission.boundary = BoundaryService(10).import_boundary("area.geojson", valid_geojson)
    source = SimulatedVehicleSource(mission, seed=42, total_steps=12)
    updates = [source.next_update() for _ in range(12)]
    event_types = [event.type.value for update in updates if update for event in update.events]
    assert "TARGET_CANDIDATE" in event_types
    assert "TARGET_CONFIRMED" in event_types
    assert "DELIVERY_COMPLETED" in event_types


def test_complete_simulated_mission_populates_review(client: TestClient, mission_id: str) -> None:
    response = client.post(f"/api/engineering/simulator/{mission_id}/complete")
    assert response.status_code == 200
    mission = client.get(f"/api/missions/{mission_id}").json()
    assert mission["state"] == "COMPLETED"
    assert mission["coverage_percent"] == 100
    vehicles = client.get(f"/api/missions/{mission_id}/vehicles").json()
    assert vehicles[0]["vehicle_id"] == "drone_01"
    review = client.get(f"/api/missions/{mission_id}/review").json()
    assert review["targets_found"] == 1
    assert review["targets_confirmed"] == 1
    assert review["deliveries_completed"] == 1

