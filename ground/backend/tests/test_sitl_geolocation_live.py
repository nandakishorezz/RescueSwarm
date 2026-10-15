"""Opt-in gold test: real ArduCopter SITL pose drives synthetic target projection/inverse localization."""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from ground.backend.app.domain.execution import ExecutionState
from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.runner import SITLMissionRunner
from rescue_core.geolocation.camera_models import CameraModel
from rescue_core.geolocation.clock_sync import ClockSyncQuality
from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import TargetObservation, TargetPixelStrategy, TargetTrackState
from rescue_core.geolocation.projection import SyntheticTargetProjector
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from tools.stage5_synthetic_validation import detection_result, nadir_extrinsics, synthetic_calibration


pytestmark = [pytest.mark.sitl, pytest.mark.sitl_geo]


@pytest.mark.skipif(os.getenv("RESCUESWARM_RUN_SITL_GEO") != "1", reason="live SITL geolocation is opt-in")
def test_real_sitl_pose_confirms_known_synthetic_ground_target(tmp_path: Path) -> None:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in [(0, 0), (42, 0), (42, 28), (0, 28)]],
        source_format="stage5_sitl_geo", area_m2=1176, area_hectares=0.1176, origin=origin,
    )
    plan = CoveragePlanner().plan("stage5-sitl-geo", boundary, CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))
    calibration, extrinsics = synthetic_calibration(), nadir_extrinsics()
    projector = SyntheticTargetProjector(CameraModel.from_calibration(calibration), extrinsics)
    target = np.array([20.0, 14.0, 0.0])
    observations: list[TargetObservation] = []
    estimates = []
    adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only", 60, 15)
    tracker = SpatiotemporalTargetTracker(
        mission_id=plan.mission_id, fusion=TargetFusionEngine(), wgs84_converter=frame.enu_to_wgs84,
        minimum_observations=3, minimum_time_span_s=0.1, association_radius_m=5,
    )
    counter = 0

    def telemetry(_snapshot, _execution) -> None:
        nonlocal counter
        subscriber = adapter.pose_subscriber
        if not subscriber:
            return
        counter += 1
        if counter % 5 == 0:
            subscriber.request_timesync()
        if subscriber.clock_sync.quality is not ClockSyncQuality.SYNCED or not subscriber.history.samples:
            return
        sample = subscriber.history.samples[-1]
        pixel = projector.project(target, sample)
        if pixel is None:
            return
        frame_host_ns = sample.autopilot_boot_time_ns - subscriber.clock_sync.offset_ns
        result, detection = detection_result(frame_host_ns, pixel, counter)
        geolocator = TargetGeolocator(
            calibration=calibration, extrinsics=extrinsics, pose_history=subscriber.history,
            clock_sync=subscriber.clock_sync, ground_model=FlatGroundModel(),
            wgs84_converter=frame.enu_to_wgs84,
            uncertainty=GeolocationUncertaintyEstimator(3, 5, 1, 0.1, 0.1, 1),
            pixel_strategy=TargetPixelStrategy.BBOX_CENTER,
        )
        observation = geolocator.geolocate(
            result, detection, "drone_01", active_width=1280, active_height=800,
            active_sensor_mode="SYNTHETIC_1280x800",
        )
        if isinstance(observation, TargetObservation):
            observations.append(observation)
            estimates.append(tracker.process(observation))

    launcher = SITLLauncher(speedup=3)
    launcher.start(origin, 0, tmp_path / "sitl_geo.log")
    try:
        runner = SITLMissionRunner(
            adapter, 60, 420, 180,
            {"GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10, "ATTITUDE_QUATERNION": 10,
             "ATTITUDE": 10, "GPS_RAW_INT": 5, "SYS_STATUS": 2, "MISSION_CURRENT": 2,
             "EXTENDED_SYS_STATE": 2, "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1},
            telemetry_callback=telemetry,
        )
        execution = runner.execute(plan, boundary)
    finally:
        launcher.stop()
    confirmed = [estimate for estimate in estimates if estimate and estimate.state is TargetTrackState.CONFIRMED]
    assert execution.state is ExecutionState.COMPLETED
    assert confirmed, f"No confirmed target from {len(observations)} visible observations"
    final = confirmed[-1]
    assert math.hypot(final.east_m - target[0], final.north_m - target[1]) < 0.1

