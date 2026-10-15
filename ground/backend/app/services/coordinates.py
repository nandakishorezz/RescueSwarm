"""Accurate WGS84 to mission-local topocentric ENU conversion."""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import Transformer
from pyproj.enums import TransformDirection

from ..domain.mission import GeoPoint


@dataclass(frozen=True)
class EnuPoint:
    east_m: float
    north_m: float
    up_m: float


class LocalCoordinateFrame:
    """WGS84 ellipsoid-centered topocentric frame anchored at mission origin."""

    def __init__(self, origin: GeoPoint):
        self.origin = origin
        altitude = origin.altitude_m or 0.0
        pipeline = (
            "+proj=pipeline "
            "+step +proj=cart +ellps=WGS84 "
            f"+step +proj=topocentric +ellps=WGS84 +lat_0={origin.latitude:.12f} "
            f"+lon_0={origin.longitude:.12f} +h_0={altitude:.4f}"
        )
        self.transformer = Transformer.from_pipeline(pipeline)

    def to_enu(self, point: GeoPoint) -> EnuPoint:
        east, north, up = self.transformer.transform(
            point.longitude, point.latitude, point.altitude_m or 0.0
        )
        return EnuPoint(east_m=east, north_m=north, up_m=up)

    def to_wgs84(self, point: EnuPoint) -> GeoPoint:
        longitude, latitude, altitude = self.transformer.transform(
            point.east_m,
            point.north_m,
            point.up_m,
            direction=TransformDirection.INVERSE,
        )
        return GeoPoint(latitude=latitude, longitude=longitude, altitude_m=altitude)

    def enu_to_wgs84(self, east_m: float, north_m: float, up_m: float = 0.0) -> GeoPoint:
        """Stage 5 callback-friendly wrapper around the existing ENU conversion."""
        return self.to_wgs84(EnuPoint(east_m=east_m, north_m=north_m, up_m=up_m))
