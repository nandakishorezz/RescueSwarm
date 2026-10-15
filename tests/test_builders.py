import pytest
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from rescue_core.simulation.world.builders import WorldBuilder
from rescue_core.simulation.profiles import load_drone_profile, load_camera_profile, load_environment_profile, SurvivorScenario, MissionWorldProfile, ScalarValue, Vector3Value

ROOT = Path(__file__).resolve().parent.parent

def test_builders_multitarget_payload():
    drone = load_drone_profile(ROOT / "simulation" / "profiles" / "rescueswarm_reference_quad.yaml")
    camera = load_camera_profile(ROOT / "simulation" / "profiles" / "ov9281_phase2_oblique_simulation.yaml")
    wind = load_environment_profile(ROOT / "simulation" / "profiles" / "perception_moderate_environment.yaml").wind
    survivors = SurvivorScenario(scenario_id="test", count=0, initial_hidden_survivors=0)
    mission = MissionWorldProfile(
        mission_id="test",
        boundary_wgs84=[(0,0), (0,1), (1,0)],
        origin_wgs84=(0, 0, 0),
        ground_elevation_m=ScalarValue(value=0, unit="m", provenance="SIMULATION_ONLY"),
        world_dimensions_m=Vector3Value(value=(160, 140, 100), unit="m", provenance="ENGINEERING_ESTIMATE"),
        launch_zone_width_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        launch_zone_length_m=ScalarValue(value=3.6576, unit="m", provenance="REFERENCE_PRESET"),
        environment_profile_id="test"
    )
    
    sdf_string = WorldBuilder().build(
        mission=mission,
        drone=drone,
        wind=wind,
        survivors=survivors,
        payload_slots=3
    )
    root = ET.fromstring(sdf_string)
    
    # 1. model names unique, detach topics unique
    models = root.findall(".//model")
    payload_names = [m.get("name") for m in models if m.get("name", "").startswith("payload_slot_")]
    assert len(payload_names) == 3
    assert len(set(payload_names)) == 3
    
    # 2. spawn positions unique, collision boxes do not overlap
    poses = []
    masses = []
    inertias = []
    cz = 0
    for model in models:
        name = model.get("name", "")
        if name.startswith("payload_slot_"):
            pose = model.findtext("pose")
            z = float(pose.split()[2])
            poses.append(z)
            
            body = model.find("link[@name='body']")
            inertial = body.find("inertial")
            mass = float(inertial.findtext("mass"))
            masses.append(mass)
            
            inertia = inertial.find("inertia")
            ixx = float(inertia.findtext("ixx"))
            iyy = float(inertia.findtext("iyy"))
            izz = float(inertia.findtext("izz"))
            inertias.append((ixx, iyy, izz))
            
            collision = body.find("collision[@name='payload_collision']/geometry/box/size")
            size = collision.text
            cz = float(size.split()[2])
            
            # verify sizes are sane
            assert cz > 0
            assert cz <= 0.02
    
    assert len(set(poses)) == 3
    poses.sort()
    clearance_1_2 = (poses[1] - cz/2.0) - (poses[0] + cz/2.0)
    clearance_2_3 = (poses[2] - cz/2.0) - (poses[1] + cz/2.0)
    assert clearance_1_2 > 0
    assert clearance_2_3 > 0
    
    # 3. mass > safe minimum, inertias finite, inertias > 0, no NaN / inf values
    for m in masses:
        assert m >= 0.05
        assert not math.isnan(m)
    
    for (ixx, iyy, izz) in inertias:
        assert ixx > 0 and iyy > 0 and izz > 0
        assert not math.isnan(ixx) and not math.isnan(iyy) and not math.isnan(izz)
        assert not math.isinf(ixx) and not math.isinf(iyy) and not math.isinf(izz)
