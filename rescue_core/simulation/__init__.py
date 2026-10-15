"""Stage 13SIM high-fidelity simulation boundary.

This package is deliberately isolated from autonomy.  World truth types live
under :mod:`rescue_core.simulation.truth` and may only be consumed by builders,
observer/evaluator code, and simulation tests.
"""

from .georef import GazeboPoint, SimulationGeoreference
from .profiles import (
    CameraProfile,
    DroneProfile,
    EnvironmentProfile,
    MissionWorldProfile,
    SimulationConfig,
    SurvivorScenario,
    load_camera_profile,
    load_drone_profile,
)
from .wind import WindSample, generate_wind_schedule
from .sensor_realism import (
    SensorProfileId, SensorRealismProfile, SensorSeeds,
    get_isolated_sensor_profile, get_sensor_realism_profile, sensor_realism_profiles,
)

__all__ = [
    "CameraProfile", "DroneProfile", "EnvironmentProfile", "GazeboPoint",
    "MissionWorldProfile", "SimulationConfig", "SimulationGeoreference",
    "SurvivorScenario", "WindSample", "generate_wind_schedule",
    "load_camera_profile", "load_drone_profile",
    "SensorProfileId", "SensorRealismProfile", "SensorSeeds",
    "get_sensor_realism_profile", "sensor_realism_profiles",
    "get_isolated_sensor_profile",
]
