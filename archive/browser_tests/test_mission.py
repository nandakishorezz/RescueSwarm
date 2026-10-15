import json
import asyncio
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.boundary_service import BoundaryService
from ground.backend.app.sitl.mission import ArduCopterMissionBuilder

def test():
    boundary = BoundaryService(10).import_boundary(
        "phase2.json",
        json.dumps({
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        }).encode(),
    )
    plan = CoveragePlanner().plan(
        "stage13sim_phase2_pixel_only",
        boundary,
        CoverageRequest(
            search_altitude_m=12.5,
            lane_spacing_m=35,
            effective_swath_m=35,
            edge_margin_m=5,
            search_speed_m_s=4.0,
            boundary_id="phase2.json",
            camera_profile_id="ov9281_phase2_oblique_simulation",
            drone_profile_id="rescueswarm_reference_quad",
        )
    )
    mission = ArduCopterMissionBuilder().build(plan, include_rtl=False)
    for i, item in enumerate(mission.items):
        print(f"{i}: {item.command}")
test()
