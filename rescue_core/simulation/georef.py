"""Single explicit Stage-5-ENU to Gazebo-world georeference."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame


@dataclass(frozen=True)
class GazeboPoint:
    x_m: float
    y_m: float
    z_m: float


class SimulationGeoreference:
    """Gazebo world is explicitly aligned X=East, Y=North, Z=Up.

    Keeping this transform explicit makes a future non-aligned world one matrix
    change rather than scattered axis swaps.
    """

    def __init__(self, origin: GeoPoint, gazebo_from_enu: np.ndarray | None = None) -> None:
        self.origin = origin
        self.local = LocalCoordinateFrame(origin)
        self.t_gazebo_from_enu = np.asarray(gazebo_from_enu if gazebo_from_enu is not None else np.eye(4), dtype=float)
        if self.t_gazebo_from_enu.shape != (4, 4):
            raise ValueError("T_gazebo_from_enu must be a 4x4 matrix")
        rotation = self.t_gazebo_from_enu[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-9) or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-9):
            raise ValueError("T_gazebo_from_enu rotation must be right-handed and orthonormal")
        self.t_enu_from_gazebo = np.linalg.inv(self.t_gazebo_from_enu)

    def enu_to_gazebo(self, point: EnuPoint) -> GazeboPoint:
        value = self.t_gazebo_from_enu @ np.array([point.east_m, point.north_m, point.up_m, 1.0])
        return GazeboPoint(*map(float, value[:3]))

    def gazebo_to_enu(self, point: GazeboPoint) -> EnuPoint:
        value = self.t_enu_from_gazebo @ np.array([point.x_m, point.y_m, point.z_m, 1.0])
        return EnuPoint(east_m=float(value[0]), north_m=float(value[1]), up_m=float(value[2]))

    def wgs84_to_gazebo(self, point: GeoPoint) -> GazeboPoint:
        return self.enu_to_gazebo(self.local.to_enu(point))

    def gazebo_to_wgs84(self, point: GazeboPoint) -> GeoPoint:
        return self.local.to_wgs84(self.gazebo_to_enu(point))
