import pytest

from ground.backend.app.domain.mission import GeoPoint
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame


def test_origin_is_zero_and_roundtrip_is_precise() -> None:
    origin = GeoPoint(latitude=12.9716, longitude=77.5946, altitude_m=920)
    frame = LocalCoordinateFrame(origin)
    zero = frame.to_enu(origin)
    assert zero.east_m == pytest.approx(0, abs=1e-6)
    assert zero.north_m == pytest.approx(0, abs=1e-6)
    point = GeoPoint(latitude=12.9722, longitude=77.5953, altitude_m=935)
    enu = frame.to_enu(point)
    assert 70 < enu.east_m < 80
    assert 60 < enu.north_m < 70
    restored = frame.to_wgs84(enu)
    assert restored.latitude == pytest.approx(point.latitude, abs=1e-9)
    assert restored.longitude == pytest.approx(point.longitude, abs=1e-9)
    assert restored.altitude_m == pytest.approx(point.altitude_m, abs=1e-5)


def test_known_enu_offset_roundtrips() -> None:
    frame = LocalCoordinateFrame(GeoPoint(latitude=0, longitude=0, altitude_m=0))
    wgs84 = frame.to_wgs84(EnuPoint(100, 100, 10))
    restored = frame.to_enu(wgs84)
    assert restored.east_m == pytest.approx(100, abs=1e-5)
    assert restored.north_m == pytest.approx(100, abs=1e-5)

