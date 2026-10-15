"""Opt-in live Stage 9S multi-ArduCopter smoke; campaign script holds full scenarios."""

import os

import pytest

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.sitl.multi_launcher import MultiSITLLauncher


@pytest.mark.sitl_fleet
@pytest.mark.skipif(os.getenv("RUN_SITL_FLEET") != "1", reason="Set RUN_SITL_FLEET=1 for live multi-SITL")
def test_two_live_arducopter_instances_have_unique_sysids(tmp_path):
    launcher = MultiSITLLauncher(
        ardupilot_home=os.getenv("ARDUPILOT_HOME", "/home/nandakishore7/ardupilot"), speedup=3,
    )
    try:
        launcher.start_fleet(2, GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584), 0, tmp_path / "multi.log")
        launcher.wait_endpoints(90)
        identities = launcher.verify_heartbeats(30)
        assert [item["system_id"] for item in identities] == [1, 2]
    finally:
        launcher.stop()
