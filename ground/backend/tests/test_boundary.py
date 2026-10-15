import json

import pytest

from ground.backend.app.services.boundary_service import BoundaryError, BoundaryService


def _geojson(coordinates: list[list[float]]) -> bytes:
    return json.dumps({"type": "Polygon", "coordinates": [coordinates]}).encode()


def test_geodesic_area_is_reasonable(valid_geojson: bytes) -> None:
    boundary = BoundaryService(10).import_boundary("site.geojson", valid_geojson)
    assert 11_000 < boundary.area_m2 < 13_000
    assert boundary.area_hectares == pytest.approx(boundary.area_m2 / 10_000)
    assert boundary.warnings == []


def test_rejects_too_few_vertices_and_self_intersection() -> None:
    service = BoundaryService(10)
    with pytest.raises(BoundaryError, match="three unique"):
        service.import_boundary("bad.geojson", _geojson([[0, 0], [1, 1], [0, 0]]))
    bow_tie = [[77.0, 12.0], [77.01, 12.01], [77.01, 12.0], [77.0, 12.01], [77.0, 12.0]]
    with pytest.raises(BoundaryError, match="invalid"):
        service.import_boundary("cross.geojson", _geojson(bow_tie))


def test_area_over_competition_limit_is_flagged() -> None:
    large = [[77.0, 12.0], [77.02, 12.0], [77.02, 12.02], [77.0, 12.02], [77.0, 12.0]]
    boundary = BoundaryService(10).import_boundary("large.geojson", _geojson(large))
    assert boundary.area_hectares > 10
    assert "exceeds configured competition limit" in boundary.warnings[0]


def test_kml_polygon_import() -> None:
    kml = b"""<?xml version='1.0'?>
    <kml xmlns='http://www.opengis.net/kml/2.2'><Placemark><Polygon><outerBoundaryIs>
    <LinearRing><coordinates>77.0,12.0,0 77.001,12.0,0 77.001,12.001,0 77.0,12.0,0</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>"""
    boundary = BoundaryService(10).import_boundary("area.kml", kml)
    assert boundary.source_format == "kml"
    assert len(boundary.vertices) == 3


def test_kml_external_entity_is_rejected() -> None:
    malicious = b"""<?xml version='1.0'?><!DOCTYPE kml [<!ENTITY xxe SYSTEM 'local-file'>]>
    <kml><Polygon><coordinates>&xxe;</coordinates></Polygon></kml>"""
    with pytest.raises(BoundaryError, match="Invalid KML"):
        BoundaryService(10).import_boundary("unsafe.kml", malicious)


def test_geojson_coordinate_order_and_range_are_validated() -> None:
    with pytest.raises(ValueError):
        BoundaryService(10).import_boundary(
            "bad.geojson", _geojson([[190, 12], [191, 12], [190, 13], [190, 12]])
        )
