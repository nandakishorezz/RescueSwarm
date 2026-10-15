"""Opt-in live Stage 8S emergency recall from actual ArduCopter GUIDED."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ground.backend.app.domain.events import EventType
from ground.backend.app.domain.execution import ExecutionState
from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.rescue_artifacts import Stage7SArtifactWriter
from ground.backend.app.sitl.rescue_runner import SITLRescueMissionRunner
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.models import SyntheticSurvivorScenario


pytestmark = [pytest.mark.sitl, pytest.mark.sitl_resilience]


@pytest.mark.skipif(os.getenv("RESCUESWARM_RUN_SITL_RESILIENCE") != "1", reason="live Stage 8S resilience SITL is opt-in")
def test_emergency_recall_during_guided_closes_drop_gate_and_lands(tmp_path: Path) -> None:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
        source_format="stage8s_recall", area_m2=1176, area_hectares=.1176, origin=origin,
    )
    plan = CoveragePlanner().plan("stage8s-live-recall", boundary, CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))
    scenario = SyntheticSurvivorScenario(
        target_id="synthetic_recall_target", true_east_m=20, true_north_m=14,
        visibility_distance_m=30, delivery_required=True, seed=8,
    )
    launcher = SITLLauncher(speedup=3)
    identity = launcher.start(origin, 0, tmp_path / "sitl_stage8s_recall.log")
    holder = {}
    events = []

    def event_callback(event):
        events.append(event)
        if event.type is EventType.GUIDED_ENTERED:
            holder["runner"].abort()

    try:
        adapter = SITLAutopilotAdapter(
            "tcp:127.0.0.1:5760", "sitl", "simulation_only",
            heartbeat_timeout_s=60, command_timeout_s=15,
        )
        runner = SITLRescueMissionRunner(
            adapter, load_rescue_config(), 60, 360, 180,
            {
                "GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10,
                "ATTITUDE_QUATERNION": 10, "ATTITUDE": 10, "GPS_RAW_INT": 5,
                "SYS_STATUS": 2, "MISSION_CURRENT": 3, "EXTENDED_SYS_STATE": 2,
                "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1,
            },
            event_callback=event_callback,
            artifacts=Stage7SArtifactWriter(tmp_path, plan.mission_id),
        )
        holder["runner"] = runner
        execution = runner.execute(plan, boundary, scenario, identity.commit)
    finally:
        launcher.stop()

    types = [event.type for event in events]
    assert execution.state is ExecutionState.ABORTED, execution.errors
    assert EventType.GUIDED_ENTERED in types
    assert EventType.DELIVERY_RELEASE_REQUESTED not in types
    assert execution.metadata["payload"] is None
    assert "GUIDED" in execution.metadata["mode_history"]
    assert "RTL" in execution.metadata["mode_history"]
    assert execution.rtl_completed and execution.landing_detected and execution.disarm_detected
    assert execution.metadata["hardware_gate_h1"] == "PENDING"
