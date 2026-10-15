"""Opt-in Stage 7S gold test against official ArduCopter SITL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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


pytestmark = [pytest.mark.sitl, pytest.mark.sitl_rescue]


@pytest.mark.skipif(os.getenv("RESCUESWARM_RUN_SITL_RESCUE") != "1", reason="live Stage 7S SITL rescue is opt-in")
def test_auto_guided_delivery_resume_rtl_gold_mission(tmp_path: Path) -> None:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
        source_format="stage7s_sitl_gold", area_m2=1176, area_hectares=.1176, origin=origin,
    )
    plan = CoveragePlanner().plan("stage7s-live-gold", boundary, CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))
    scenario = SyntheticSurvivorScenario(
        target_id="synthetic_survivor_01", true_east_m=20, true_north_m=14,
        visibility_distance_m=30, delivery_required=True, seed=7,
    )
    launcher = SITLLauncher(speedup=3)
    identity = launcher.start(origin, 0, tmp_path / "sitl_rescue.log")
    artifacts = Stage7SArtifactWriter(tmp_path, plan.mission_id)
    try:
        adapter = SITLAutopilotAdapter(
            "tcp:127.0.0.1:5760", "sitl", "simulation_only",
            heartbeat_timeout_s=60, command_timeout_s=15,
        )
        runner = SITLRescueMissionRunner(
            adapter, load_rescue_config(), 60, 480, 180,
            {
                "GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10,
                "ATTITUDE_QUATERNION": 10, "ATTITUDE": 10, "GPS_RAW_INT": 5,
                "SYS_STATUS": 2, "MISSION_CURRENT": 3, "EXTENDED_SYS_STATE": 2,
                "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1,
            },
            artifacts=artifacts,
        )
        execution = runner.execute(plan, boundary, scenario, identity.commit)
    finally:
        launcher.stop()
    payload = execution.metadata.get("payload") or {}
    assert execution.state is ExecutionState.COMPLETED, execution.errors
    assert payload.get("release_succeeded") and payload.get("verification_succeeded")
    assert execution.metadata["target"]["observation_count"] >= 3
    assert execution.metadata["resume_requested_seq"] is not None
    assert execution.metadata["resume_actual_seq"] >= execution.metadata["resume_requested_seq"]
    assert execution.rtl_completed and execution.landing_detected and execution.disarm_detected
    history = execution.metadata["mode_history"]
    assert history.count("AUTO") >= 2 and "GUIDED" in history and "RTL" in history
    assert history.index("AUTO") < history.index("GUIDED") < len(history) - 1
    assert execution.actual_coverage_percent >= 98
    assert execution.metadata["hardware_gate_h1"] == "PENDING"
