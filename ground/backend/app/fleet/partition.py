"""Deterministic contiguous workload partitioning over Stage 3 coverage segments."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

from shapely.geometry import LineString

from ..planning.models import CoveragePlan, CoverageSegment, EnuCoordinate, PathKind, PlanValidation
from .models import (
    CoverageSegmentWork,
    CoverageWorkState,
    FleetPlan,
    PartitionMetrics,
    SectorAssignment,
)


class FleetPartitionError(ValueError):
    pass


class FleetCoveragePartitioner:
    """Minimax linear partitioner.

    Stage 3 already orders a connected, safe boustrophedon route. Splitting that
    order into contiguous ranges preserves locality and avoids cross-sector
    interleaving. Dynamic programming minimizes the largest range cost.
    """

    VERSION = "rescueswarm-fleet-contiguous-minimax-v1"

    def partition(
        self,
        plan: CoveragePlan,
        vehicle_ids: list[str],
        launch_points_enu: dict[str, tuple[float, float]] | None = None,
    ) -> FleetPlan:
        vehicle_ids = list(vehicle_ids)
        if len(vehicle_ids) < 2:
            raise FleetPartitionError("Stage 9S fleet partition requires at least two vehicles")
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise FleetPartitionError("Vehicle IDs must be unique")
        if len(plan.segments) < len(vehicle_ids):
            raise FleetPartitionError("Coverage plan has fewer segments than fleet vehicles")
        launch_points_enu = launch_points_enu or {vehicle: (0.0, 0.0) for vehicle in vehicle_ids}
        works = self._work_segments(plan)
        ranges = self._linear_minimax_ranges([item.total_cost for item in works], len(vehicle_ids))
        assignments: list[SectorAssignment] = []
        vehicle_plans: dict[str, CoveragePlan] = {}
        per_cost: dict[str, float] = {}
        for vehicle_id, (start, end) in zip(vehicle_ids, ranges, strict=True):
            selected = works[start:end]
            source_segments = plan.segments[start:end]
            for work in selected:
                work.assigned_vehicle_id = vehicle_id
                work.state = CoverageWorkState.ASSIGNED
            cost = sum(item.total_cost for item in selected)
            assignments.append(SectorAssignment(
                vehicle_id=vehicle_id,
                segment_ids=[item.segment_id for item in selected],
                estimated_cost=cost,
                estimated_search_time_s=sum(item.estimated_search_time_s for item in selected),
            ))
            per_cost[vehicle_id] = cost + self._launch_transit(
                launch_points_enu.get(vehicle_id, (0.0, 0.0)), selected[0].entry_point
            )
            vehicle_plans[vehicle_id] = self._vehicle_plan(plan, vehicle_id, source_segments)

        errors = self.validate(plan, vehicle_ids, works, assignments)
        if errors:
            raise FleetPartitionError("Fleet partition validation failed: " + "; ".join(errors))
        total = sum(per_cost.values())
        maximum, minimum = max(per_cost.values()), min(per_cost.values())
        mean = total / len(per_cost)
        fingerprint_payload = {
            "version": self.VERSION,
            "mission": plan.plan_fingerprint or plan.mission_id,
            "assignments": [(item.vehicle_id, item.segment_ids) for item in assignments],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        global_fingerprint = plan.plan_fingerprint or hashlib.sha256(
            plan.model_dump_json(exclude={"created_at"}).encode()
        ).hexdigest()
        metrics = PartitionMetrics(
            total_cost=total,
            per_vehicle_cost=per_cost,
            maximum_vehicle_cost=maximum,
            minimum_vehicle_cost=minimum,
            imbalance_percent=0 if mean == 0 else (maximum - minimum) / mean * 100,
            transit_distance_m=sum(item.transit_distance_m for item in works),
            cross_sector_transit_m=0,
            disconnected_assignments=0,
            estimated_makespan_s=max(
                vehicle_plans[vehicle].estimated_search_time_s for vehicle in vehicle_ids
            ),
        )
        return FleetPlan(
            mission_id=plan.mission_id,
            mission_fingerprint=global_fingerprint,
            partition_fingerprint=fingerprint,
            fleet_size=len(vehicle_ids),
            vehicle_ids=vehicle_ids,
            global_coverage_plan=plan,
            segments=works,
            assignments=assignments,
            vehicle_plans=vehicle_plans,
            launch_order=vehicle_ids,
            metrics=metrics,
        )

    @staticmethod
    def _launch_transit(start: tuple[float, float], end: EnuCoordinate) -> float:
        return math.hypot(start[0] - end.east_m, start[1] - end.north_m)

    @staticmethod
    def _work_segments(plan: CoveragePlan) -> list[CoverageSegmentWork]:
        result: list[CoverageSegmentWork] = []
        for index, segment in enumerate(plan.segments):
            coordinates = [(point.east_m, point.north_m) for point in segment.points_enu]
            distance = LineString(coordinates).length
            search_distance = distance if segment.kind is PathKind.SEARCH else 0.0
            transit_distance = distance if segment.kind is PathKind.TRANSIT else 0.0
            turn_cost = plan.configuration.turn_penalty_m if segment.kind is PathKind.TRANSIT else 0.0
            before = plan.segments[index - 1].segment_id if index else None
            after = plan.segments[index + 1].segment_id if index + 1 < len(plan.segments) else None
            result.append(CoverageSegmentWork(
                segment_id=segment.segment_id,
                kind=segment.kind,
                points_enu=deepcopy(segment.points_enu),
                search_distance_m=search_distance,
                transit_distance_m=transit_distance,
                estimated_search_time_s=distance / plan.configuration.search_speed_m_s,
                turn_cost_m=turn_cost,
                total_cost=distance + turn_cost,
                entry_point=deepcopy(segment.points_enu[0]),
                exit_point=deepcopy(segment.points_enu[-1]),
                adjacent_segment_ids=[value for value in (before, after) if value],
            ))
        return result

    @staticmethod
    def _linear_minimax_ranges(costs: list[float], parts: int) -> list[tuple[int, int]]:
        """Exact deterministic contiguous partition minimizing maximum cost."""
        count = len(costs)
        prefix = [0.0]
        for cost in costs:
            prefix.append(prefix[-1] + cost)
        infinity = float("inf")
        dp = [[infinity] * (parts + 1) for _ in range(count + 1)]
        cut = [[0] * (parts + 1) for _ in range(count + 1)]
        dp[0][0] = 0
        for used in range(1, parts + 1):
            for end in range(used, count + 1):
                for start in range(used - 1, end):
                    candidate = max(dp[start][used - 1], prefix[end] - prefix[start])
                    if candidate < dp[end][used] - 1e-9:
                        dp[end][used] = candidate
                        cut[end][used] = start
        ranges: list[tuple[int, int]] = []
        end = count
        for used in range(parts, 0, -1):
            start = cut[end][used]
            ranges.append((start, end))
            end = start
        return list(reversed(ranges))

    @staticmethod
    def _vehicle_plan(plan: CoveragePlan, vehicle_id: str, segments: list[CoverageSegment]) -> CoveragePlan:
        assigned: list[CoverageSegment] = []
        path_enu: list[EnuCoordinate] = []
        path_wgs84 = []
        for segment in segments:
            copy = segment.model_copy(deep=True, update={"assigned_vehicle_id": vehicle_id})
            assigned.append(copy)
            if not path_enu:
                path_enu.extend(copy.points_enu)
                path_wgs84.extend(copy.points_wgs84)
            else:
                path_enu.extend(copy.points_enu[1:])
                path_wgs84.extend(copy.points_wgs84[1:])
        distance = sum(
            LineString([(point.east_m, point.north_m) for point in segment.points_enu]).length
            for segment in assigned
        )
        search_lines = sum(segment.kind is PathKind.SEARCH for segment in assigned)
        search_distance = sum(
            LineString([(point.east_m, point.north_m) for point in segment.points_enu]).length
            for segment in assigned if segment.kind is PathKind.SEARCH
        )
        global_search_distance = sum(
            LineString([(point.east_m, point.north_m) for point in segment.points_enu]).length
            for segment in plan.segments if segment.kind is PathKind.SEARCH
        )
        share = 100 * search_distance / global_search_distance if global_search_distance else 0
        result = plan.model_copy(deep=True, update={
            "mission_id": f"{plan.mission_id}:{vehicle_id}",
            "segments": assigned,
            "path_points_enu": path_enu,
            "path_points_wgs84": path_wgs84,
            "estimated_path_length_m": max(distance, 1e-6),
            "estimated_search_time_s": max(distance / plan.configuration.search_speed_m_s, 1e-6),
            "number_of_search_lines": max(search_lines, 1),
            "number_of_turns": max(0, search_lines - 1),
            "cost_m": max(distance + max(0, search_lines - 1) * plan.configuration.turn_penalty_m, 1e-6),
            "validation": PlanValidation(
                valid=True,
                warnings=["Per-vehicle coverage is a validated subset of the globally validated plan"],
                planned_coverage_percent=min(100, share),
                covered_area_m2=plan.validation.covered_area_m2 * share / 100,
                missed_area_m2=max(0, plan.validation.covered_area_m2 * (1 - share / 100)),
            ),
            "metadata": {
                **plan.metadata,
                "fleet_vehicle_id": vehicle_id,
                "global_plan_fingerprint": plan.plan_fingerprint,
                "coverage_share_percent": share,
                "stage9s": True,
            },
            "mission_fingerprint": None,
        })
        payload = result.model_dump(mode="json", exclude={"created_at", "plan_fingerprint", "mission_fingerprint"})
        result.plan_fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return result

    @staticmethod
    def validate(
        plan: CoveragePlan,
        vehicle_ids: list[str],
        works: list[CoverageSegmentWork],
        assignments: list[SectorAssignment],
    ) -> list[str]:
        errors: list[str] = []
        expected = [segment.segment_id for segment in plan.segments]
        actual = [segment_id for assignment in assignments for segment_id in assignment.segment_ids]
        if sorted(actual) != sorted(expected):
            errors.append("Every global segment must be assigned exactly once")
        if len(actual) != len(set(actual)):
            errors.append("Duplicate segment ownership detected")
        if {assignment.vehicle_id for assignment in assignments} != set(vehicle_ids):
            errors.append("Assignment vehicles do not match configured fleet")
        if [work.segment_id for work in works] != expected:
            errors.append("Partition changed global segment order")
        for assignment in assignments:
            indices = sorted(expected.index(value) for value in assignment.segment_ids)
            if indices != list(range(indices[0], indices[-1] + 1)):
                errors.append(f"{assignment.vehicle_id} assignment is disconnected")
        return errors
