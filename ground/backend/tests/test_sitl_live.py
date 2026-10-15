"""Opt-in gold test: official ArduCopter actually flies the generated AUTO mission."""

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
from ground.backend.app.sitl.artifacts import Stage3ArtifactWriter
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.runner import SITLMissionRunner


pytestmark = pytest.mark.sitl


@pytest.mark.skipif(os.getenv("RESCUESWARM_RUN_SITL") != "1", reason="live SITL is opt-in")
def test_official_arducopter_executes_generated_search_and_lands(tmp_path: Path) -> None:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    shape = [(0, 0), (42, 0), (42, 28), (0, 28)]
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(east, north, 0)) for east, north in shape],
        source_format="live_sitl_fixture",
        area_m2=1176,
        area_hectares=0.1176,
        origin=origin,
    )
    plan = CoveragePlanner().plan(
        "live-sitl-gold",
        boundary,
        CoverageRequest(
            search_altitude_m=10,
            lane_spacing_m=10,
            effective_swath_m=10,
            edge_margin_m=2,
            search_speed_m_s=6,
            min_planned_coverage_percent=98,
        ),
    )
    launcher = SITLLauncher(speedup=3)
    identity = launcher.start(origin, 0, tmp_path / "sitl.log")
    try:
        adapter = SITLAutopilotAdapter(
            "tcp:127.0.0.1:5760", "sitl", "simulation_only",
            heartbeat_timeout_s=60, command_timeout_s=15,
        )
        runner = SITLMissionRunner(
            adapter,
            readiness_timeout_s=60,
            mission_timeout_s=420,
            rtl_timeout_s=180,
            message_rates_hz={
                "GLOBAL_POSITION_INT": 10, "ATTITUDE": 10, "GPS_RAW_INT": 5,
                "SYS_STATUS": 2, "MISSION_CURRENT": 2, "EXTENDED_SYS_STATE": 2,
                "EKF_STATUS_REPORT": 2,
                "HOME_POSITION": 1,
            },
            artifacts=Stage3ArtifactWriter(tmp_path, plan.mission_id),
        )
        execution = runner.execute(plan, boundary, identity.commit)
    finally:
        launcher.stop()

    assert execution.state is ExecutionState.COMPLETED, execution.errors
    assert execution.rtl_completed
    assert execution.landing_detected
    assert execution.disarm_detected
    assert execution.waypoints_reached
    assert execution.actual_path_length_m > 0
    assert execution.actual_coverage_percent > 0
