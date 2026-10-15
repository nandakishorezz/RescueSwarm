import math
from shapely.geometry import Polygon, Point

class DummyController:
    def __init__(self, terrain_map, geofence):
        self.terrain_map = terrain_map # dict (x,y) -> z or a callable
        self.boundary_enu = geofence
        
    def _get_terrain_elevation(self, east, north):
        return self.terrain_map(east, north)
        
    def _compute_safe_delivery_offset(self, target_east: float, target_north: float) -> tuple[float, float]:
        candidates = []
        radii = [1.5, 2.5, 3.5, 4.5]
        angles = [0, 45, 90, 135, 180, 225, 270, 315]
        for r in radii:
            for angle_deg in angles:
                rad = math.radians(angle_deg)
                dx = r * math.sin(rad)
                dy = r * math.cos(rad)
                ce = target_east + dx
                cn = target_north + dy
                if self.boundary_enu and not self.boundary_enu.covers(Point(ce, cn)): continue
                try:
                    z_center = self._get_terrain_elevation(ce, cn)
                    z_n = self._get_terrain_elevation(ce, cn + 0.5)
                    z_e = self._get_terrain_elevation(ce + 0.5, cn)
                    slope_n = abs(z_n - z_center) / 0.5
                    slope_e = abs(z_e - z_center) / 0.5
                    max_slope = max(slope_n, slope_e)
                    if max_slope > 0.2: continue
                except Exception:
                    continue
                candidates.append((dx, dy, max_slope, r))
        if not candidates: raise RuntimeError("Target-service safety gate: No safe environmental delivery zone found for target.")
        candidates.sort(key=lambda c: (c[2], c[3]))
        return candidates[0][0], candidates[0][1]


def test_flat_terrain():
    ctrl = DummyController(lambda e, n: 0.0, Polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)]))
    dx, dy = ctrl._compute_safe_delivery_offset(0, 0)
    assert abs(dx - 0.0) < 1e-6 and abs(dy - 1.5) < 1e-6, f"Expected (0, 1.5) but got {dx, dy}"
    print("test_flat_terrain passed")

def test_geofence_rejection():
    # Geofence only allows points strictly to the East
    geofence = Polygon([(0.1, -5), (10, -5), (10, 5), (0.1, 5)])
    ctrl = DummyController(lambda e, n: 0.0, geofence)
    dx, dy = ctrl._compute_safe_delivery_offset(0, 0)
    # R=1.5, angle=90 (East) -> dx=1.5, dy=0
    assert abs(dx - 1.06066) < 1e-4 and abs(dy - 1.06066) < 1e-4, f"Got {dx}, {dy}"
    print("test_geofence_rejection passed")


def test_slope_rejection():
    # A slope of 0.2 everywhere except exactly at North R=2.5 where slope is 0.0
    def terrain(e, n):
        # slope is high if e > 0
        return e * 0.5 
    
    ctrl = DummyController(terrain, Polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)]))
    dx, dy = ctrl._compute_safe_delivery_offset(0, 0)
    # Points with e <= 0 have 0 slope, but we evaluate e + 0.5 for slope_e.
    # If e=0, e+0.5=0.5 -> terrain is 0.25 -> slope is 0.25 / 0.5 = 0.5 > 0.2. So e=0 is rejected!
    # So we must pick a point where e <= -0.5 to have slope_e <= 0.
    # The first valid point is angle=270 (West), R=1.5 -> dx = -1.5, dy=0
    assert abs(dx - (-1.5)) < 1e-6 and abs(dy - 0.0) < 1e-6, f"Got {dx}, {dy}"
    print("test_slope_rejection passed")

def test_no_safe_candidate():
    ctrl = DummyController(lambda e, n: e * 0.5 + n * 0.5, Polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)]))
    try:
        ctrl._compute_safe_delivery_offset(0, 0)
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "No safe environmental delivery zone" in str(e)
        print("test_no_safe_candidate passed")

if __name__ == "__main__":
    test_flat_terrain()
    test_geofence_rejection()
    test_slope_rejection()
    test_no_safe_candidate()
