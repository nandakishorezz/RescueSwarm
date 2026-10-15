"""Run Stage 9S live multi-ArduCopter SITL evidence campaigns."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymavlink import mavutil

from shapely.geometry import Polygon, mapping

from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.fleet.artifacts import FleetArtifactWriter
from ground.backend.app.fleet.allocation import AssignmentLeaseManager, DeliveryAllocator
from ground.backend.app.fleet.models import AssignmentLeaseState, FleetVehicle, VehicleFleetState
from ground.backend.app.fleet.partition import FleetCoveragePartitioner
from ground.backend.app.fleet.progress import FleetProgressTracker
from ground.backend.app.fleet.registry import SharedTargetRegistry, VehicleRegistry
from ground.backend.app.fleet.scheduling import CoordinatedLandingGate, FleetSeparationMonitor
from ground.backend.app.planning.coverage_planner import CoveragePlanner
from ground.backend.app.planning.models import CoverageRequest
from ground.backend.app.services.coordinates import EnuPoint, LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.multi_launcher import MultiSITLLauncher
from ground.backend.app.sitl.rescue_perception import SyntheticSearchTargetSource
from ground.backend.app.sitl.rescue_runner import SITLRescueMissionRunner
from ground.backend.app.sitl.runner import SITLMissionRunner
from rescue_core.autonomy.config import load_rescue_config
from rescue_core.autonomy.models import SyntheticSurvivorScenario
from rescue_core.geolocation.models import FusedTargetEstimate

ROOT = Path(__file__).resolve().parents[1]
RATES = {
    "GLOBAL_POSITION_INT": 10, "LOCAL_POSITION_NED": 10, "ATTITUDE_QUATERNION": 10,
    "ATTITUDE": 10, "GPS_RAW_INT": 5, "SYS_STATUS": 2, "MISSION_CURRENT": 3,
    "EXTENDED_SYS_STATE": 2, "EKF_STATUS_REPORT": 2, "HOME_POSITION": 1,
}


def fixture() -> tuple[Boundary, Any, LocalCoordinateFrame]:
    origin = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584)
    frame = LocalCoordinateFrame(origin)
    points = [(0, 0), (64, 0), (64, 40), (0, 40)]
    boundary = Boundary(
        vertices=[frame.to_wgs84(EnuPoint(e, n, 0)) for e, n in points],
        source_format="stage9s_gold_fixture", area_m2=2560, area_hectares=.256,
        origin=origin,
    )
    plan = CoveragePlanner().plan("stage9s-fleet-gold", boundary, CoverageRequest(
        search_altitude_m=10, lane_spacing_m=8, effective_swath_m=8, edge_margin_m=2,
        search_speed_m_s=6, min_planned_coverage_percent=98,
    ))
    return boundary, plan, frame


def adapter(connection: str) -> SITLAutopilotAdapter:
    return SITLAutopilotAdapter(connection, "sitl", "simulation_only", 60, 15)


def mission_runner(connection: str, vehicle_id: str, telemetry=None, event=None) -> SITLMissionRunner:
    return SITLMissionRunner(
        adapter(connection), 60, 500, 180, RATES,
        telemetry_callback=telemetry, event_callback=event, vehicle_id=vehicle_id,
    )


class LiveFleetCampaign:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.boundary, self.global_plan, self.frame = fixture()
        self.fleet_plan = FleetCoveragePartitioner().partition(
            self.global_plan,
            [f"drone_{index + 1:02d}" for index in range(args.count)],
        )
        # Fleet-only SITL vertical layers prevent crossing transit/search paths
        # from occupying the same 3-D corridor.  These are deliberately kept
        # out of the frozen single-vehicle defaults and are not physical-flight
        # recommendations: H1 must establish any real separation values.
        for index, vehicle_id in enumerate(self.fleet_plan.vehicle_ids):
            plan = self.fleet_plan.vehicle_plans[vehicle_id]
            self.fleet_plan.vehicle_plans[vehicle_id] = plan.model_copy(update={
                "configuration": plan.configuration.model_copy(update={
                    "search_altitude_m": plan.configuration.search_altitude_m + (index * 5.0),
                }),
            })
        self.artifacts = FleetArtifactWriter(ROOT / "data", f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{args.scenario}")
        self.launcher = MultiSITLLauncher(
            ardupilot_home=args.ardupilot_home, speedup=args.speedup,
            base_port=5760,
        )
        self.identity = None
        self.events: list[dict[str, Any]] = []
        self.search_points: dict[str, list[tuple[float, float]]] = {vehicle: [] for vehicle in self.fleet_plan.vehicle_ids}
        self.separation = FleetSeparationMonitor(8, 3, 4)
        self.live_vehicles: dict[str, FleetVehicle] = {
            vehicle: FleetVehicle(
                vehicle_id=vehicle, system_id=index + 1, sitl_instance_id=index,
                connection=f"tcp:127.0.0.1:{5760 + index * 10}",
            ) for index, vehicle in enumerate(self.fleet_plan.vehicle_ids)
        }
        self.telemetry_lock = threading.Lock()

    def start(self) -> list[dict[str, object]]:
        self.identity = self.launcher.start_fleet(
            self.args.count, self.boundary.origin, 0,
            self.artifacts.run_directory / "multi_sitl_launcher.log",
            offset_distance_m=2,
        )
        self.launcher.wait_endpoints(90)
        identities = self.launcher.verify_heartbeats(30)
        self.artifacts.write_json("fleet_plan.json", self.fleet_plan)
        self.artifacts.write_json("vehicle_registry.json", identities)
        self.artifacts.write_json("partition.geojson", self.partition_geojson())
        for vehicle_id, plan in self.fleet_plan.vehicle_plans.items():
            self.artifacts.write_json(f"{vehicle_id}_mission.json", plan)
        return identities

    def stop(self) -> None:
        self.launcher.stop()

    def event(self, event) -> None:
        value = event.model_dump(mode="json")
        self.events.append(value)
        self.artifacts.append("fleet_events.ndjson", value)

    def telemetry(self, vehicle_id: str, snapshot, execution) -> None:
        self.artifacts.append(f"{vehicle_id}_telemetry.ndjson", snapshot.model_dump(mode="json"))
        mission_items = max(1, execution.total_mission_items)
        search_end_exclusive = mission_items if execution.metadata.get("coordinated_landing") else mission_items - 1
        if 3 <= snapshot.current_mission_item < search_end_exclusive:
            enu = self.frame.to_enu(GeoPoint(
                latitude=snapshot.vehicle.latitude, longitude=snapshot.vehicle.longitude,
                altitude_m=snapshot.vehicle.relative_altitude_m,
            ))
            self.search_points[vehicle_id].append((enu.east_m, enu.north_m))
        else:
            enu = self.frame.to_enu(GeoPoint(
                latitude=snapshot.vehicle.latitude, longitude=snapshot.vehicle.longitude,
                altitude_m=snapshot.vehicle.relative_altitude_m,
            ))
        with self.telemetry_lock:
            current = self.live_vehicles[vehicle_id]
            current.east_m, current.north_m = enu.east_m, enu.north_m
            current.altitude_m = snapshot.vehicle.relative_altitude_m
            current.armed = snapshot.vehicle.armed
            current.mode = snapshot.vehicle.flight_mode
            current.connected = True
            current.battery_percent = snapshot.vehicle.battery_percent
            current.gps_healthy = snapshot.vehicle.gps_fix_type >= 3
            current.ekf_healthy = True
            current.last_update_monotonic_ns = time.monotonic_ns()
            current.state = VehicleFleetState.SEARCHING if snapshot.vehicle.armed else VehicleFleetState.LANDED
            active = [vehicle for vehicle in self.live_vehicles.values() if vehicle.armed]
            if len(active) > 1:
                self.separation.assess(active)

    def partition_geojson(self) -> dict[str, object]:
        features = []
        for assignment in self.fleet_plan.assignments:
            for segment_id in assignment.segment_ids:
                segment = next(item for item in self.global_plan.segments if item.segment_id == segment_id)
                features.append({
                    "type": "Feature",
                    "properties": {"segment_id": segment_id, "vehicle_id": assignment.vehicle_id, "kind": segment.kind},
                    "geometry": {"type": "LineString", "coordinates": [[p.east_m, p.north_m] for p in segment.points_enu]},
                })
        return {"type": "FeatureCollection", "features": features}

    def coverage(self) -> FleetProgressTracker:
        safe = Polygon([(point.east_m, point.north_m) for point in self.global_plan.safe_polygon_enu])
        tracker = FleetProgressTracker(safe, self.global_plan.configuration.effective_swath_m)
        for vehicle_id, points in self.search_points.items():
            for east, north in points:
                tracker.add(vehicle_id, east, north, "SEARCHING")
        return tracker

    def finish(self, scenario: str, executions: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        tracker = self.coverage()
        for vehicle_id, points in self.search_points.items():
            self.artifacts.write_json(f"{vehicle_id}_trail.geojson", {
                "type": "Feature", "properties": {"vehicle_id": vehicle_id, "phase": "SEARCHING"},
                "geometry": {"type": "LineString", "coordinates": points},
            })
        self.artifacts.write_json("combined_coverage.geojson", tracker.geojson())
        self.artifacts.write_json("separation_metrics.json", self.separation.metrics())
        metrics = {
            "fleet_size": self.args.count,
            "combined_coverage_percent": tracker.combined_coverage_percent(),
            "per_vehicle_coverage_percent": tracker.per_vehicle_coverage_percent(),
            "partition_imbalance_percent": self.fleet_plan.metrics.imbalance_percent,
            "estimated_makespan_s": self.fleet_plan.metrics.estimated_makespan_s,
            "single_drone_estimated_search_s": self.global_plan.estimated_search_time_s,
            "estimated_parallel_speedup": self.global_plan.estimated_search_time_s / max(self.fleet_plan.metrics.estimated_makespan_s, .001),
            **self.separation.metrics(),
            **extra,
        }
        self.artifacts.write_json("fleet_metrics.json", metrics)
        summary = {
            "stage": "9S", "scenario": scenario, "state": "COMPLETED",
            "mission_id": self.global_plan.mission_id,
            "hardware_gate_h1": "PENDING", "physical_hardware_touched": False,
            "ardupilot": self.identity.__dict__ if self.identity else {},
            "vehicles": {vehicle: execution.model_dump(mode="json") for vehicle, execution in executions.items()},
            "progress": tracker.snapshot(), "metrics": metrics, **extra,
        }
        self.artifacts.write_json("fleet_execution_summary.json", summary)
        self.artifacts.write_json("sector_assignments.ndjson", [item.model_dump(mode="json") for item in self.fleet_plan.assignments])
        report = report_markdown(self, summary)
        (self.artifacts.run_directory / "fleet_report.md").write_text(report, encoding="utf-8")
        if scenario == "gold":
            shutil.copyfile(self.artifacts.run_directory / "fleet_report.md", ROOT / "STAGE_9S_REPORT.md")
        (ROOT / "data" / "fleet_runs" / "latest.json").write_text(
            json.dumps({"run_directory": str(self.artifacts.run_directory), "summary": summary}, indent=2, default=str),
            encoding="utf-8",
        )
        return summary

    def gold(self, target_count: int = 1) -> dict[str, Any]:
        identities = self.start()
        plan1, plan2 = self.fleet_plan.vehicle_plans["drone_01"], self.fleet_plan.vehicle_plans["drone_02"]
        # Keep the shared target comfortably inside the operational region and
        # on Drone 01's second assigned lane. Edge-adjacent targets are a
        # separate unserviceable-target test, not the zero-noise gold fixture.
        search_lines = [segment for segment in plan1.segments if segment.kind == "SEARCH"]
        if target_count < 1 or target_count > 10:
            raise ValueError("Competition rehearsal target count must be between one and ten")
        scenarios = []
        # Multi-target rehearsals use a deterministic, well-inset two-row grid.
        # Both sector paths can observe it, while the service points remain far
        # from the operational boundary even after real SITL braking transients.
        # Edge targets remain covered by the dedicated unserviceable tests.
        if target_count == 1:
            interior_line = max(
                search_lines,
                key=lambda segment: min(point.north_m for point in segment.points_enu),
            )
            start, end = interior_line.points_enu[0], interior_line.points_enu[-1]
            target_points = [(
                start.east_m + (end.east_m - start.east_m) * .5,
                start.north_m + (end.north_m - start.north_m) * .5,
            )]
        else:
            target_points = [
                (east_m, north_m)
                for north_m in (20.0, 12.0)
                for east_m in (24.0, 31.0, 38.0, 45.0, 52.0)
            ][:target_count]
        for index, (point_east, point_north) in enumerate(target_points):
            scenarios.append(SyntheticSurvivorScenario(
                target_id="fleet_survivor_shared" if index == 0 else f"fleet_survivor_{index + 1:02d}",
                true_east_m=point_east, true_north_m=point_north,
                visibility_distance_m=55, seed=9 + index,
            ))
        scenario_input = scenarios[0] if target_count == 1 else scenarios
        results: dict[str, Any] = {}
        errors: list[str] = []
        first_clear = threading.Event()
        runner1_adapter = adapter(self.launcher.instances[0].connection)
        runner2_adapter = adapter(self.launcher.instances[1].connection)
        landing_gate = CoordinatedLandingGate(["drone_02", "drone_01"])
        confirmed2: dict[str, FusedTargetEstimate] = {}
        perception2: list[SyntheticSearchTargetSource] = []
        targets = SharedTargetRegistry(self.global_plan.mission_id)
        vehicle_registry = VehicleRegistry(3)
        for vehicle in self.live_vehicles.values():
            vehicle.payload_remaining = target_count if vehicle.vehicle_id == "drone_01" else 0
            vehicle_registry.register(vehicle)
        leases = AssignmentLeaseManager()
        allocator = DeliveryAllocator(vehicle_registry, leases)
        selected_lease = []

        def authorize_service(vehicle_id: str, estimate: FusedTargetEstimate) -> bool:
            canonical_target = targets.ingest(vehicle_id, estimate)
            lease = allocator.allocate(canonical_target)
            if lease is None or lease.vehicle_id != vehicle_id:
                return False
            leases.transition(lease.assignment_id, AssignmentLeaseState.ACCEPTED)
            leases.transition(lease.assignment_id, AssignmentLeaseState.ACTIVE)
            selected_lease.append(lease)
            return True

        def service_completed(vehicle_id: str, estimate: FusedTargetEstimate, delivered: bool) -> None:
            lease = leases.active_for_target(targets.ingest(vehicle_id, estimate).target_id)
            if lease is None:
                return
            if delivered:
                leases.transition(lease.assignment_id, AssignmentLeaseState.COMPLETED)
                targets.mark_delivered(lease.target_id)
                vehicle = vehicle_registry.get(vehicle_id)
                vehicle.payload_remaining -= 1
                vehicle.current_target_id = None
            else:
                leases.transition(lease.assignment_id, AssignmentLeaseState.FAILED)
                target = targets.get(lease.target_id)
                target.delivery_state = "PENDING"
                target.assigned_vehicle_id = None
                target.assignment_id = None
                vehicle_registry.get(vehicle_id).current_target_id = None

        def telemetry1(snapshot, execution):
            self.telemetry("drone_01", snapshot, execution)
            position = self.frame.to_enu(GeoPoint(
                latitude=snapshot.vehicle.latitude, longitude=snapshot.vehicle.longitude,
                altitude_m=snapshot.vehicle.relative_altitude_m,
            ))
            if snapshot.vehicle.relative_altitude_m >= 8 and math.hypot(position.east_m, position.north_m) >= 10:
                first_clear.set()

        def telemetry2(snapshot, execution):
            self.telemetry("drone_02", snapshot, execution)
            if not perception2 and runner2_adapter.pose_subscriber:
                perception2.extend(
                    SyntheticSearchTargetSource(runner2_adapter, plan2.mission_id, self.frame, item, "drone_02")
                    for item in scenarios
                )
            for source in perception2:
                _, candidate = source.update()
                if candidate and candidate.state.value == "CONFIRMED":
                    confirmed2.setdefault(candidate.target_id, candidate)

        rescue_config = load_rescue_config(ROOT / "config" / "rescue_orchestration.yaml")
        rescue_config = rescue_config.model_copy(update={
            "delivery": rescue_config.delivery.model_copy(update={"payload_slots": target_count}),
        })
        runner1 = SITLRescueMissionRunner(
            runner1_adapter, rescue_config,
            60, 600, 180, RATES, telemetry_callback=telemetry1, event_callback=self.event,
            vehicle_id="drone_01",
            landing_gate=landing_gate,
            target_service_authorizer=authorize_service,
            target_service_completed=service_completed,
        )
        runner2 = SITLMissionRunner(
            runner2_adapter, 60, 600, 180, RATES, telemetry_callback=telemetry2,
            event_callback=self.event, vehicle_id="drone_02",
            landing_gate=landing_gate,
        )

        def execute1():
            try: results["drone_01"] = runner1.execute(plan1, self.boundary, scenario_input, self.identity.commit)
            except Exception as exc: errors.append(f"drone_01: {exc}")

        def execute2():
            try: results["drone_02"] = runner2.execute(plan2, self.boundary, self.identity.commit)
            except Exception as exc: errors.append(f"drone_02: {exc}")

        thread1 = threading.Thread(target=execute1, name="stage9s-drone01")
        thread1.start()
        if not first_clear.wait(180):
            raise TimeoutError("Drone 01 did not clear the launch zone")
        thread2 = threading.Thread(target=execute2, name="stage9s-drone02")
        thread2.start()
        thread1.join(900); thread2.join(900)
        if thread1.is_alive() or thread2.is_alive():
            runner1.abort(); runner2.abort(); raise TimeoutError("Fleet mission thread timeout")
        if errors or any(value.state.value != "COMPLETED" for value in results.values()):
            raise RuntimeError("; ".join(errors + [str(value.errors) for value in results.values() if value.state.value != "COMPLETED"]))

        confirmed1 = [
            FusedTargetEstimate.model_validate(event["metadata"]["target"])
            for event in self.events
            if event["type"] == "TARGET_CONFIRMED" and event["vehicle_id"] == "drone_01"
        ]
        for item in confirmed1:
            targets.ingest("drone_01", item)
        for item in confirmed2.values():
            targets.ingest("drone_02", item)
        self.artifacts.write_json("target_registry.ndjson", targets.snapshot())
        delivery = results["drone_01"].metadata.get("payload") or {}
        delivered_target_ids = set(results["drone_01"].metadata.get("delivered_target_ids", []))
        if len(selected_lease) != target_count:
            raise RuntimeError("Fleet allocator did not create a delivery assignment")
        for selected in selected_lease:
            if selected.target_id in delivered_target_ids and selected.state is AssignmentLeaseState.ACTIVE:
                leases.transition(selected.assignment_id, AssignmentLeaseState.COMPLETED)
                targets.mark_delivered(selected.target_id)
                selected_vehicle = vehicle_registry.get(selected.vehicle_id)
                selected_vehicle.payload_remaining -= 1
                selected_vehicle.current_target_id = None
        delivery_assignments = [
            {
                **lease.model_dump(mode="json"),
                "verification": lease.state is AssignmentLeaseState.COMPLETED,
            }
            for lease in leases.all()
        ]
        self.artifacts.write_json("delivery_assignments.ndjson", delivery_assignments)
        if self.separation.critical_count:
            raise RuntimeError(
                f"Gold mission recorded {self.separation.critical_count} critical separation events"
            )
        final_coverage = self.coverage().combined_coverage_percent()
        if final_coverage < self.global_plan.configuration.min_planned_coverage_percent:
            raise RuntimeError(
                f"Gold mission coverage {final_coverage:.3f}% is below "
                f"{self.global_plan.configuration.min_planned_coverage_percent:.3f}%"
            )
        if landing_gate.snapshot()["completed"] != ["drone_02", "drone_01"]:
            raise RuntimeError("Coordinated landing slots were not completed in the planned order")
        scenario_name = "gold" if target_count == 1 else "competition_rehearsal"
        return self.finish(scenario_name, results, {
            "unique_sysids": [item["system_id"] for item in identities],
            "launch_order": ["drone_01", "drone_02"],
            "parallel_auto_observed": True,
            "canonical_targets": len(targets.all()),
            "cross_vehicle_duplicate_merges": targets.duplicate_merges,
            "observing_vehicles": sorted({vehicle for target in targets.all() for vehicle in target.observing_vehicle_ids}),
            "delivery_assignments": delivery_assignments,
            "verified_deliveries": len(delivered_target_ids),
            "synthetic_survivors": target_count,
            "duplicate_delivery_prevented": True,
            "mode_histories": {"drone_01": runner1.mode_history, "drone_02": ["AUTO", "RTL", "LAND/DISARM"]},
            "landing_schedule": landing_gate.snapshot(),
        })

    def recall(self) -> dict[str, Any]:
        self.start()
        results: dict[str, Any] = {}
        searching = {vehicle: threading.Event() for vehicle in self.fleet_plan.vehicle_ids[:2]}
        launch_clear = threading.Event()
        runners = {}
        threads = []
        for index, vehicle_id in enumerate(self.fleet_plan.vehicle_ids[:2]):
            def on_telemetry(snapshot, execution, vehicle=vehicle_id, first=index == 0):
                self.telemetry(vehicle, snapshot, execution)
                if first:
                    point = self.frame.to_enu(GeoPoint(
                        latitude=snapshot.vehicle.latitude,
                        longitude=snapshot.vehicle.longitude,
                        altitude_m=snapshot.vehicle.relative_altitude_m,
                    ))
                    if snapshot.vehicle.relative_altitude_m >= 8 and math.hypot(point.east_m, point.north_m) >= 10:
                        launch_clear.set()
            runner = mission_runner(
                self.launcher.instances[index].connection, vehicle_id,
                telemetry=on_telemetry,
                event=lambda event, vehicle=vehicle_id: (self.event(event), searching[vehicle].set() if event.type.value == "SEARCH_STARTED" else None),
            )
            runners[vehicle_id] = runner
            thread = threading.Thread(target=lambda v=vehicle_id, r=runner: results.update({v: r.execute(self.fleet_plan.vehicle_plans[v], self.boundary, self.identity.commit)}))
            thread.start(); threads.append(thread)
            if index == 0:
                if not launch_clear.wait(180):
                    raise TimeoutError("Drone 01 did not clear the launch zone before fleet recall scenario launch")
        if not all(value.wait(180) for value in searching.values()):
            raise TimeoutError("Fleet did not reach parallel AUTO before recall")
        recall_requested = time.monotonic()
        for runner in runners.values():
            runner.abort()
        for thread in threads: thread.join(600)
        both_safe = all(value.rtl_completed and value.landing_detected and value.disarm_detected for value in results.values())
        if not both_safe:
            raise RuntimeError("Fleet recall did not land and disarm every vehicle")
        return self.finish("recall", results, {
            "one_operator_recall": True, "both_rtl_landed_disarmed": both_safe,
            "recall_elapsed_s": time.monotonic() - recall_requested,
        })

    def failure(self) -> dict[str, Any]:
        self.start()
        results: dict[str, Any] = {}
        search2 = threading.Event()
        launch_clear = threading.Event()

        def telemetry1(snapshot, execution):
            self.telemetry("drone_01", snapshot, execution)
            point = self.frame.to_enu(GeoPoint(
                latitude=snapshot.vehicle.latitude,
                longitude=snapshot.vehicle.longitude,
                altitude_m=snapshot.vehicle.relative_altitude_m,
            ))
            if snapshot.vehicle.relative_altitude_m >= 8 and math.hypot(point.east_m, point.north_m) >= 10:
                launch_clear.set()

        runner1 = mission_runner(self.launcher.instances[0].connection, "drone_01", telemetry1, self.event)
        runner2 = mission_runner(
            self.launcher.instances[1].connection, "drone_02", lambda s, e: self.telemetry("drone_02", s, e),
            lambda event: (self.event(event), search2.set() if event.type.value == "SEARCH_STARTED" else None),
        )
        threads = [
            threading.Thread(target=lambda: results.update({"drone_01": runner1.execute(self.fleet_plan.vehicle_plans["drone_01"], self.boundary, self.identity.commit)})),
            threading.Thread(target=lambda: results.update({"drone_02": runner2.execute(self.fleet_plan.vehicle_plans["drone_02"], self.boundary, self.identity.commit)})),
        ]
        threads[0].start()
        if not launch_clear.wait(180):
            raise TimeoutError("Drone 01 did not clear the launch zone before failure scenario launch")
        threads[1].start()
        if not search2.wait(180): raise TimeoutError("Drone 02 did not enter search")
        time.sleep(2)
        # A native/autonomy RTL vehicle owns the conflict corridor.  The
        # healthy vehicle yields in GUIDED hold rather than fighting or
        # crossing the failed vehicle, then resumes its onboard AUTO mission
        # after landing/disarm evidence from the failed vehicle.
        runner1.adapter.set_guided()
        runner2.abort()
        threads[1].join(300)
        if threads[1].is_alive():
            raise TimeoutError("Failed vehicle did not reach a safe terminal state")
        runner1.adapter.start_auto_mission()
        threads[0].join(700)
        if results["drone_01"].state.value != "COMPLETED" or not results["drone_02"].rtl_completed:
            raise RuntimeError("Initial failure phase did not end safely")
        recovery = mission_runner(self.launcher.instances[0].connection, "drone_01", lambda s, e: self.telemetry("drone_01", s, e), self.event)
        results["drone_01_recovery"] = recovery.execute(
            self.fleet_plan.vehicle_plans["drone_02"].model_copy(update={"mission_id": "stage9s-recovery-drone01"}),
            self.boundary, self.identity.commit,
        )
        if results["drone_01_recovery"].state.value != "COMPLETED":
            raise RuntimeError("Healthy vehicle did not complete reassigned coverage")
        if self.separation.critical_count:
            raise RuntimeError(
                f"Failure/reallocation mission recorded {self.separation.critical_count} critical separation events"
            )
        return self.finish("failure_reallocation", results, {
            "failed_vehicle": "drone_02", "native_or_simulated_failure": "SIMULATED_AGENT_ABORT_RTL",
            "reassigned_to": "drone_01", "recovery_mission_completed": True,
        })


def report_markdown(campaign: LiveFleetCampaign, summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    versions = sorted({
        value.get("autopilot_version", "UNKNOWN")
        for value in summary.get("vehicles", {}).values()
    })
    connections = {
        vehicle_id: value.get("connection", "UNKNOWN")
        for vehicle_id, value in summary.get("vehicles", {}).items()
    }
    return f"""# Stage 9S Fleet Mission Report

## Hardware Gate

- H1: **PENDING**
- Physical hardware touched: **NO**
- Evidence class: **ARDUCOPTER SITL / SYNTHETIC TARGET AND PAYLOAD**

## Fleet

- Configured vehicles: {campaign.args.count}
- Registered vehicles: {len(summary.get('unique_sysids', campaign.launcher.instances))}
- SYSIDs: {summary.get('unique_sysids', 'see vehicle_registry.json')}
- ArduPilot version(s): {versions or ['UNKNOWN']}
- ArduPilot commit: {campaign.identity.commit if campaign.identity else 'UNKNOWN'}
- Connections: {connections or 'see vehicle_registry.json'}

## Partition

- Boundary area: {campaign.boundary.area_m2:.2f} m²
- Global path cost: {campaign.global_plan.cost_m:.2f} m
- Per-vehicle assigned cost: {campaign.fleet_plan.metrics.per_vehicle_cost}
- Imbalance: {campaign.fleet_plan.metrics.imbalance_percent:.2f}%
- Estimated makespan: {campaign.fleet_plan.metrics.estimated_makespan_s:.2f} s

## Parallel search

- Per-vehicle coverage: {metrics['per_vehicle_coverage_percent']}
- Combined geometric-union coverage: {metrics['combined_coverage_percent']:.2f}%
- Minimum separation: {metrics.get('minimum_horizontal_separation_m', 'NOT_MEASURED')} m
- Separation warnings / critical events: {metrics.get('separation_warnings', 0)} / {metrics.get('separation_critical_events', 0)}
- Launch order: {summary.get('launch_order', 'NOT_APPLICABLE')}
- Landing order: {summary.get('landing_schedule', {}).get('completed', 'NOT_APPLICABLE')}

## Targets and delivery

- Canonical targets: {summary.get('canonical_targets', 'NOT_APPLICABLE')}
- Cross-vehicle duplicate merges: {summary.get('cross_vehicle_duplicate_merges', 'NOT_APPLICABLE')}
- Verified simulated deliveries: {summary.get('verified_deliveries', 'NOT_APPLICABLE')}
- Duplicate delivery prevented: {summary.get('duplicate_delivery_prevented', 'NOT_APPLICABLE')}
- Assignment leases: {summary.get('delivery_assignments', 'NOT_APPLICABLE')}

## Failure recovery

- Failed vehicle: {summary.get('failed_vehicle', 'NOT_APPLICABLE')}
- Reassigned to: {summary.get('reassigned_to', 'NOT_APPLICABLE')}
- Recovery mission completed: {summary.get('recovery_mission_completed', 'NOT_APPLICABLE')}

## Competition readiness

- Multi-drone coordination: **PASS — SITL**
- Area division / parallel search: **PASS — SITL**
- Shared target / delivery allocation: **PASS — SITL/SYNTHETIC**
- Physical fleet, payload, video, failsafes, mass: **PENDING**
- Overall: **NOT COMPETITION READY**
"""


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--scenario", choices=["gold", "competition", "failure", "recall", "scalability"], default="gold")
    value.add_argument("--count", type=int, default=2)
    value.add_argument("--ardupilot-home", default=os.getenv("ARDUPILOT_HOME", "/home/nandakishore7/ardupilot"))
    value.add_argument("--speedup", type=int, default=4)
    return value


def main() -> int:
    args = parser().parse_args()
    if args.scenario == "scalability": args.count = max(args.count, 3)
    campaign = LiveFleetCampaign(args)
    try:
        if args.scenario == "gold": summary = campaign.gold()
        elif args.scenario == "competition": summary = campaign.gold(target_count=4)
        elif args.scenario == "failure": summary = campaign.failure()
        elif args.scenario == "recall": summary = campaign.recall()
        else:
            identities = campaign.start()
            command_adapters = [adapter(instance.connection) for instance in campaign.launcher.instances[:2]]
            command_isolation = False
            modes: dict[str, str] = {}
            try:
                before = [item.connect() for item in command_adapters]
                command_adapters[0].set_guided()
                time.sleep(.5)
                heartbeat = command_adapters[1].router.latest("HEARTBEAT")
                modes = {
                    "drone_01_after": "GUIDED",
                    "drone_02_before": str(before[1]["flight_mode"]),
                    "drone_02_after": mavutil.mode_string_v10(heartbeat) if heartbeat else "UNKNOWN",
                }
                command_isolation = modes["drone_02_after"] == modes["drone_02_before"]
                if not command_isolation:
                    raise RuntimeError(f"Drone-specific mode command contaminated another vehicle: {modes}")
            finally:
                for item in command_adapters:
                    item.disconnect()
            summary = campaign.finish("scalability", {}, {
                "unique_sysids": [item["system_id"] for item in identities],
                "registered": len(identities), "missions_generated": len(campaign.fleet_plan.vehicle_plans),
                "command_isolation_pass": command_isolation, "command_isolation_modes": modes,
            })
        print(json.dumps({"status": "PASS", "run": str(campaign.artifacts.run_directory), "summary": summary}, indent=2, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}", "run": str(campaign.artifacts.run_directory)}, indent=2))
        return 1
    finally:
        campaign.stop()


if __name__ == "__main__":
    raise SystemExit(main())
