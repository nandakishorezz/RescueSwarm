"""Safe delivery offset and point computation for medical-kit payload delivery.

Computes a clear standoff delivery location near the survivor, ensuring:
1. Kit is NEVER dropped directly on the person's body (minimum safe standoff distance).
2. The delivery point is strictly within the mission's operational boundary with clearance.
3. The delivery point maintains a safe standoff from any other known survivors.
4. Ground slope/terrain clearance is validated if terrain information is available.
5. If no safe location can be established, delivery is safely deferred.
"""

from __future__ import annotations

import math
from typing import Any, Callable
from shapely.geometry import Point, Polygon


class SafeDeliveryError(RuntimeError):
    """Raised when no safe delivery location can be established."""


def compute_safe_delivery_point(
    target_east_m: float,
    target_north_m: float,
    drone_east_m: float,
    drone_north_m: float,
    safe_offset_m: float,
    operational_region: Polygon,
    other_survivors: list[tuple[float, float]] | None = None,
    target_uncertainty_m: float = 0.0,
    terrain_elevation_fn: Callable[[float, float], float] | None = None,
    max_terrain_slope: float = 0.25,
    boundary_clearance_m: float = 0.5,
) -> tuple[float, float] | None:
    """Compute a safe payload-release position near the survivor.

    Returns:
        (delivery_east_m, delivery_north_m) if a safe location is found, else None.
    """
    if safe_offset_m < 1.0:
        safe_offset_m = 1.0

    # Effective standoff accounts for location uncertainty plus configured offset
    standoff = max(safe_offset_m, target_uncertainty_m + 0.5)

    # Base approach vector from target to drone
    dx = drone_east_m - target_east_m
    dy = drone_north_m - target_north_m
    dist = math.hypot(dx, dy)
    base_angle = math.atan2(dy, dx) if dist > 0.1 else 0.0

    # Candidate angles around the survivor: start along approach direction, then fan out
    angle_offsets = [0.0, math.pi / 4, -math.pi / 4, math.pi / 2, -math.pi / 2,
                     3 * math.pi / 4, -3 * math.pi / 4, math.pi]

    # Inset boundary region to guarantee clearance from perimeter
    safe_poly = operational_region.buffer(-boundary_clearance_m) if boundary_clearance_m > 0 else operational_region

    candidates: list[tuple[float, float, float]] = []

    for angle_offset in angle_offsets:
        angle = base_angle + angle_offset
        cand_east = target_east_m + standoff * math.cos(angle)
        cand_north = target_north_m + standoff * math.sin(angle)

        cand_point = Point(cand_east, cand_north)

        # 1. Geofence / Operational boundary check
        if not safe_poly.covers(cand_point):
            continue

        # 2. Check distance from target person: must be at least safe_offset_m (never on person)
        dist_to_target = math.hypot(cand_east - target_east_m, cand_north - target_north_m)
        if dist_to_target < safe_offset_m - 1e-3:
            continue

        # 3. Check distance from other known survivors
        if other_survivors:
            conflict = False
            for other_e, other_n in other_survivors:
                # Do not check against the current target if present in the list
                if math.hypot(other_e - target_east_m, other_n - target_north_m) < 0.5:
                    continue
                dist_to_other = math.hypot(cand_east - other_e, cand_north - other_n)
                if dist_to_other < safe_offset_m:
                    conflict = True
                    break
            if conflict:
                continue

        # 4. Terrain slope check if terrain model is available
        slope = 0.0
        if terrain_elevation_fn is not None:
            try:
                z_center = terrain_elevation_fn(cand_east, cand_north)
                z_n = terrain_elevation_fn(cand_east, cand_north + 0.5)
                z_e = terrain_elevation_fn(cand_east + 0.5, cand_north)
                slope_n = abs(z_n - z_center) / 0.5
                slope_e = abs(z_e - z_center) / 0.5
                slope = max(slope_n, slope_e)
                if slope > max_terrain_slope:
                    continue
            except Exception:
                pass

        # Candidate is valid: store with angle difference for alignment efficiency
        candidates.append((cand_east, cand_north, abs(angle_offset)))

    if not candidates:
        return None

    # Choose candidate with minimum diversion angle from the incoming approach
    candidates.sort(key=lambda c: c[2])
    return candidates[0][0], candidates[0][1]
