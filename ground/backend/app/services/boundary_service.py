"""Safe boundary parsing, geometry validation, and geodesic area calculation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import PurePath

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError
from pyproj import Geod
from shapely.geometry import Polygon
from shapely.validation import explain_validity

from ..domain.mission import Boundary, GeoPoint


class BoundaryError(ValueError):
    """Operator-safe boundary import or validation failure."""


class BoundaryParser(ABC):
    extensions: frozenset[str]

    @abstractmethod
    def parse(self, content: bytes) -> list[GeoPoint]: ...


class GeoJsonBoundaryParser(BoundaryParser):
    extensions = frozenset({".geojson", ".json"})

    def parse(self, content: bytes) -> list[GeoPoint]:
        try:
            root = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BoundaryError(f"Invalid GeoJSON: {exc}") from exc
        if not isinstance(root, dict):
            raise BoundaryError("GeoJSON root must be an object")
        geometry = root
        if root.get("type") == "Feature":
            geometry = root.get("geometry")
        elif root.get("type") == "FeatureCollection":
            features = root.get("features", [])
            polygons = [item.get("geometry") for item in features if isinstance(item, dict) and item.get("geometry", {}).get("type") == "Polygon"]
            if len(polygons) != 1:
                raise BoundaryError("GeoJSON FeatureCollection must contain exactly one Polygon")
            geometry = polygons[0]
        if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
            raise BoundaryError("GeoJSON must contain one Polygon geometry")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise BoundaryError("GeoJSON Polygon has no exterior ring")
        if len(coordinates) > 1:
            raise BoundaryError("Polygon holes are not supported in the Stage 2 mission boundary")
        ring = coordinates[0]
        if not isinstance(ring, list):
            raise BoundaryError("GeoJSON exterior ring is invalid")
        try:
            return [GeoPoint(longitude=float(pair[0]), latitude=float(pair[1])) for pair in ring]
        except (IndexError, TypeError, ValueError, ValidationError) as exc:
            raise BoundaryError("GeoJSON coordinates must be [longitude, latitude]") from exc


class KmlBoundaryParser(BoundaryParser):
    extensions = frozenset({".kml"})

    def parse(self, content: bytes) -> list[GeoPoint]:
        try:
            root = ElementTree.fromstring(content)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise BoundaryError(f"Invalid KML XML: {exc}") from exc
        coordinate_nodes = [
            node for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "coordinates"
            and any(parent.tag.rsplit("}", 1)[-1] == "Polygon" for parent in _ancestors(root, node))
        ]
        if len(coordinate_nodes) != 1 or not coordinate_nodes[0].text:
            raise BoundaryError("KML must contain exactly one Polygon coordinate ring")
        points: list[GeoPoint] = []
        try:
            for token in coordinate_nodes[0].text.split():
                values = token.split(",")
                altitude = float(values[2]) if len(values) > 2 and values[2] else None
                points.append(
                    GeoPoint(longitude=float(values[0]), latitude=float(values[1]), altitude_m=altitude)
                )
        except (IndexError, ValueError) as exc:
            raise BoundaryError("KML coordinates must use longitude,latitude[,altitude]") from exc
        return points


def _ancestors(root: ElementTree.Element, target: ElementTree.Element) -> list[ElementTree.Element]:
    """Return ancestors without accepting XPath or external XML features."""

    found: list[ElementTree.Element] = []

    def visit(node: ElementTree.Element, stack: list[ElementTree.Element]) -> bool:
        if node is target:
            found.extend(stack)
            return True
        return any(visit(child, [*stack, node]) for child in list(node))

    visit(root, [])
    return found


class BoundaryService:
    def __init__(self, max_area_hectares: float):
        self.max_area_hectares = max_area_hectares
        parsers = (GeoJsonBoundaryParser(), KmlBoundaryParser())
        self.parsers = {extension: parser for parser in parsers for extension in parser.extensions}
        self.geod = Geod(ellps="WGS84")

    def import_boundary(self, filename: str, content: bytes) -> Boundary:
        extension = PurePath(filename).suffix.lower()
        parser = self.parsers.get(extension)
        if parser is None:
            raise BoundaryError(f"Unsupported boundary extension: {extension or '(none)'}")
        return self.validate(parser.parse(content), extension.lstrip("."))

    def validate(self, points: list[GeoPoint], source_format: str) -> Boundary:
        if points and points[0].latitude == points[-1].latitude and points[0].longitude == points[-1].longitude:
            points = points[:-1]
        unique = {(point.latitude, point.longitude) for point in points}
        if len(unique) < 3:
            raise BoundaryError("Boundary requires at least three unique vertices")
        if len(unique) != len(points):
            raise BoundaryError("Boundary contains duplicate vertices")
        if len(points) > 10_000:
            raise BoundaryError("Boundary exceeds the 10,000 vertex safety limit")
        polygon = Polygon([(point.longitude, point.latitude) for point in points])
        if not polygon.is_valid:
            raise BoundaryError(f"Boundary geometry is invalid: {explain_validity(polygon)}")
        if polygon.is_empty or polygon.area == 0:
            raise BoundaryError("Boundary has zero area")
        longitudes = [point.longitude for point in points]
        latitudes = [point.latitude for point in points]
        area_m2, _ = self.geod.polygon_area_perimeter(longitudes, latitudes)
        area_m2 = abs(area_m2)
        if area_m2 <= 0:
            raise BoundaryError("Boundary geodesic area is zero")
        area_hectares = area_m2 / 10_000
        warnings: list[str] = []
        if area_hectares > self.max_area_hectares:
            warnings.append(
                f"Boundary area {area_hectares:.2f} ha exceeds configured competition limit "
                f"{self.max_area_hectares:.2f} ha"
            )
        origin_point = polygon.representative_point()
        return Boundary(
            vertices=points,
            source_format=source_format,
            area_m2=area_m2,
            area_hectares=area_hectares,
            origin=GeoPoint(latitude=origin_point.y, longitude=origin_point.x),
            warnings=warnings,
        )

    @staticmethod
    def as_geojson(boundary: Boundary) -> dict[str, object]:
        ring = [[point.longitude, point.latitude] for point in boundary.vertices]
        ring.append(ring[0])
        return {"type": "Polygon", "coordinates": [ring]}
