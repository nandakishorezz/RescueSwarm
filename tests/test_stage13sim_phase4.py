from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from ground.backend.app.sitl.payload import GazeboPayloadAdapter
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.payload import PayloadError, PayloadInventory
from rescue_core.simulation.world.builders import GazeboVehicleModelBuilder, WorldBuilder
from scripts.run_stage13sim_phase2 import build_definition


class _Autopilot:
    def __init__(self) -> None:
        self.commands = []

    def command_servo(self, servo: int, pwm: int) -> None:
        self.commands.append((servo, pwm))


def test_phase4_payload_is_physical_and_detachable() -> None:
    definition = build_definition("phase4-sdf", survivor_count=1, payload_slots=1)
    vehicle = GazeboVehicleModelBuilder().build(definition.drone, definition.camera, payload_slots=1)
    world = WorldBuilder().build(
        definition.mission, definition.drone, definition.environment.wind,
        definition.survivors, payload_slots=1,
    )
    assert "gz-sim-detachable-joint-system" in vehicle
    assert "airframe::base_link" in vehicle
    assert "/rescueswarm/payload/slot_1/detach" in vehicle
    assert '<model name="payload_slot_1">' in world
    assert "<mass>0.2</mass>" in world
    assert "<size>0.2 0.1 0.05</size>" in world


def test_gazebo_payload_release_is_actuator_first_and_exactly_once(monkeypatch, tmp_path) -> None:
    autopilot = _Autopilot()
    class MockProcess:
        def __init__(self):
            self.stdin = SimpleNamespace(write=lambda x: None, flush=lambda: None)
            self.lines = ["READY\n", "PREWARMED\n", "PREWARMED\n", "PREWARMED\n", "SUCCESS /test\n"]
            self.stdout = SimpleNamespace(readline=lambda: self.lines.pop(0) if self.lines else "")

    monkeypatch.setattr(
        "ground.backend.app.sitl.payload.subprocess.Popen",
        lambda *args, **kwargs: MockProcess()
    )
    inventory = PayloadInventory(1)
    slot = inventory.reserve("target")
    payload = GazeboPayloadAdapter(autopilot, tmp_path)  # type: ignore[arg-type]
    evidence = payload.release(
        slot, vehicle_east_m=1, vehicle_north_m=2, relative_target_error_m=.5,
        altitude_m=4.5, ground_speed_m_s=.1, target_uncertainty_m=1.3,
        release_sim_time_ns=10,
    )
    assert autopilot.commands == [(9, 1900)]
    assert evidence.actuator_acknowledged and evidence.mechanism_acknowledged
    assert payload.verify(slot, evidence)
    with pytest.raises(PayloadError):
        payload.release(slot, vehicle_east_m=0, vehicle_north_m=0,
                        relative_target_error_m=0, altitude_m=4.5,
                        ground_speed_m_s=0, target_uncertainty_m=1)


def test_phase4_profile_has_bounded_fresh_visual_gate() -> None:
    config = load_rescue_config("config/phase4_rescue_orchestration.yaml")
    assert config.reacquisition.search_radius_m <= 5
    assert config.reacquisition.standoff_m == 2.0
    assert config.reacquisition.timeout_s == 90
    assert config.alignment.observation_stale_s == 0.75
    assert config.alignment.target_pixel_y_ratio == 0.5
    assert config.delivery.delivery_altitude_m == 2.0
    assert config.delivery.max_horizontal_speed_at_drop_m_s == 0.5
    assert config.alignment.deadband_m == 0.1
    assert config.alignment.alignment_radius_m == 0.5
    assert config.alignment.stable_dwell_s == 2.0
    assert config.guided.setpoint_rate_hz <= 5
    assert config.delivery.delivery_altitude_m < config.guided.approach_altitude_m


def test_phase4_service_has_no_truth_or_evaluator_import() -> None:
    import ground.backend.app.sitl.phase4_service as service

    source = inspect.getsource(service).lower()
    assert "simulation.truth" not in source
    assert "simulation.evaluator" not in source
    assert "survivor_id" not in source
    assert "set_model_pose" not in source
    assert "teleport" not in source
