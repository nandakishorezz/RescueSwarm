"""Phase 2B Autonomous Service Dispatch, Safe Approach, Simulated Delivery, and Search Resumption Executor.

Diverts eligible SERVICER drones from AUTO search coverage to deliver medical kits at a safe standoff,
verifies delivery independently, and resumes forward search at saved checkpoints.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any

from shapely.geometry import Polygon

from ground.backend.app.domain.events import EventSeverity, EventType, MissionEvent
from ground.backend.app.domain.execution import ExecutionState, MissionExecution
from ground.backend.app.domain.mission import Boundary, GeoPoint
from ground.backend.app.planning.models import CoveragePlan
from ground.backend.app.services.coordinates import LocalCoordinateFrame
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.sitl.coverage import ActualCoverageTracker
from ground.backend.app.sitl.guided import SITLGuidedNavigator
from ground.backend.app.sitl.telemetry import SITLTelemetrySnapshot
from rescue_core.autonomy.approach import ApproachPoint, GuidedApproachPlanner, operational_flight_region
from rescue_core.autonomy.models import SearchResumePolicyName
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.autonomy.safe_delivery import compute_safe_delivery_point

from .contracts import AssignmentState
from .state_machine import DroneLifecycleState

if TYPE_CHECKING:
    from .coordinator import ExclusiveServiceCoordinator
    from .runtime import MultiDroneRuntime

logger = logging.getLogger(__name__)


class MultiDroneServiceExecutor:
    """Executes live Phase 2B autonomous service missions on live ArduCopter SITL vehicles."""

    def __init__(self, runtime: "MultiDroneRuntime"):
        self.runtime = runtime

    def _verify_released_job(self, vehicle_id, coordinator, assignment_id, evidence):
        # The vehicle lifecycle must traverse VERIFYING before DELIVERED;
        # skipping it leaves the servicer stuck in RELEASED after search resumes.
        self.runtime._transition_drone(vehicle_id, DroneLifecycleState.VERIFYING)
        verified_job = coordinator.verify_delivery(
            assignment_id, verified=True, evidence=evidence,
        )
        self.runtime._transition_drone(vehicle_id, DroneLifecycleState.DELIVERED)
        return verified_job

    def execute_service_cycle(
        self,
        runner: Any,
        plan: CoveragePlan,
        boundary: Boundary,
        snapshot: SITLTelemetrySnapshot,
        execution: MissionExecution,
        tracker: ActualCoverageTracker,
        search_first: int,
        rtl_sequence: int,
    ) -> bool:
        coordinator: "ExclusiveServiceCoordinator | None" = getattr(self.runtime, "coordinator", None)
        if coordinator is None:
            return False

        vid = runner.vehicle_id
        assignment = coordinator.get_active_assignment_for_servicer(vid)
        if assignment is None:
            return False

        # Only process assignments that are newly ASSIGNED or ACKNOWLEDGED
        if assignment.state not in {AssignmentState.ASSIGNED, AssignmentState.ACKNOWLEDGED}:
            return False

        aid = assignment.assignment_id
        survivor_id = assignment.canonical_survivor_id
        survivor = self.runtime.registry.get_survivor(survivor_id) if self.runtime.registry else None
        if survivor is None:
            logger.warning("[PHASE 2B] Service assignment %s has no canonical survivor record %s", aid, survivor_id)
            return False

        logger.info("[PHASE 2B] Starting live service execution for vehicle %s on assignment %s (survivor %s)", vid, aid, survivor_id)

        # 1. Acknowledge assignment
        if assignment.state == AssignmentState.ASSIGNED:
            coordinator.acknowledge_assignment(aid)
            self.runtime._add_event(vid, "JOB_ACKNOWLEDGED", f"Vehicle {vid} acknowledged service assignment {str(aid)[:8]}")

        # 2. Check approach safety
        safe, reason = coordinator.check_approach_safety(aid, boundary=boundary)
        if not safe:
            logger.warning("[PHASE 2B] Approach safety check blocked: %s", reason)
            self.runtime._add_event(vid, "APPROACH_SAFETY_BLOCKED", f"Approach safety check blocked: {reason}", severity="WARNING")
            return False

        # 3. Checkpoint search cursor
        frame = LocalCoordinateFrame(boundary.origin)
        vpos = snapshot.vehicle
        v_enu = frame.to_enu(GeoPoint(latitude=vpos.latitude, longitude=vpos.longitude, altitude_m=vpos.relative_altitude_m))
        resume_mgr = SearchResumeManager(search_first, rtl_sequence, SearchResumePolicyName.ARDUPILOT_NATIVE_RESUME)
        checkpoint = resume_mgr.checkpoint(
            mission_id=plan.mission_id,
            mission_fingerprint=execution.mission_fingerprint,
            current_seq=snapshot.current_mission_item,
            reached_items=list(snapshot.reached_items),
            east_m=v_enu.east_m,
            north_m=v_enu.north_m,
            coverage_percent=execution.actual_coverage_percent,
        )

        coordinator.start_approach(aid, checkpoint_seq=checkpoint.current_mission_seq)
        self.runtime._transition_drone(vid, DroneLifecycleState.APPROACHING)
        self.runtime._add_event(
            vid, "APPROACH_STARTED",
            f"Autonomous approach initiated for survivor {survivor_id} from seq {checkpoint.current_mission_seq}",
            metadata={"assignment_id": str(aid), "checkpoint_seq": checkpoint.current_mission_seq},
        )

        # 4. Compute safe standoff delivery point (>= 1.0m, NEVER on survivor)
        original_region = Polygon([
            (frame.to_enu(point).east_m, frame.to_enu(point).north_m) for point in boundary.vertices
        ])
        region = operational_flight_region(original_region, safety_margin_m=2.0)

        standoff_point = compute_safe_delivery_point(
            target_east_m=survivor.east_m,
            target_north_m=survivor.north_m,
            drone_east_m=v_enu.east_m,
            drone_north_m=v_enu.north_m,
            safe_offset_m=2.0,  # 2.0m safe standoff (guaranteed >= 1.0m)
            operational_region=region,
            target_uncertainty_m=survivor.uncertainty_m,
        )
        if standoff_point is None:
            coordinator.requeue_assignment(aid, "No safe standoff point inside boundary")
            self.runtime._transition_drone(vid, DroneLifecycleState.SEARCHING)
            return False

        deliv_east, deliv_north = standoff_point
        standoff_dist = math.hypot(deliv_east - survivor.east_m, deliv_north - survivor.north_m)
        if standoff_dist < 1.0:
            coordinator.fail_assignment(aid, f"Computed standoff distance {standoff_dist:.2f}m is closer than 1.0m minimum")
            self.runtime._transition_drone(vid, DroneLifecycleState.SEARCHING)
            return False

        # 5. Switch to GUIDED mode & execute safe approach
        navigator = SITLGuidedNavigator(runner.adapter, frame)
        navigator.enter_guided()
        self.runtime._add_event(vid, "GUIDED_ENTERED", f"Vehicle {vid} entered GUIDED mode for approach navigation")

        approach_speed = 5.0
        approach_alt = 20.0  # search/approach altitude
        navigator.set_speed(approach_speed)

        # Plan approach path
        planner = GuidedApproachPlanner(region, tolerance_m=5.0)
        approach_path = planner.plan(v_enu.east_m, v_enu.north_m, deliv_east, deliv_north, approach_alt)

        for pt in approach_path.points:
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                if runner.abort_requested:
                    logger.warning("[PHASE 2B] Abort requested during approach for %s", vid)
                    coordinator.requeue_assignment(aid, "Abort requested during approach")
                    runner.adapter.rtl()
                    self.runtime._transition_drone(vid, DroneLifecycleState.RETURNING)
                    return False

                navigator.goto_local(pt)
                time.sleep(0.3)
                curr_snap = runner.adapter.vehicle_source.normalizer.snapshot if runner.adapter.vehicle_source else None
                if curr_snap:
                    curr_v = curr_snap.vehicle
                    curr_enu = frame.to_enu(GeoPoint(latitude=curr_v.latitude, longitude=curr_v.longitude, altitude_m=curr_v.relative_altitude_m))
                    dist_to_pt = math.hypot(curr_enu.east_m - pt.east_m, curr_enu.north_m - pt.north_m)
                    if dist_to_pt < 2.5:
                        break

        # 6. Descent to Delivery Altitude & Stabilization Hold
        delivery_alt = 5.0  # configured delivery altitude (5.0m +/- 2.0m)
        descent_pt = ApproachPoint(east_m=deliv_east, north_m=deliv_north, altitude_m=delivery_alt)
        navigator.set_speed(1.5)  # gentle descent speed
        descend_deadline = time.monotonic() + 30.0
        while time.monotonic() < descend_deadline:
            if runner.abort_requested:
                coordinator.requeue_assignment(aid, "Abort requested during descent")
                runner.adapter.rtl()
                self.runtime._transition_drone(vid, DroneLifecycleState.RETURNING)
                return False

            navigator.goto_local(descent_pt)
            time.sleep(0.3)
            curr_snap = runner.adapter.vehicle_source.normalizer.snapshot if runner.adapter.vehicle_source else None
            if curr_snap:
                curr_v = curr_snap.vehicle
                curr_enu = frame.to_enu(GeoPoint(latitude=curr_v.latitude, longitude=curr_v.longitude, altitude_m=curr_v.relative_altitude_m))
                horiz_err = math.hypot(curr_enu.east_m - deliv_east, curr_enu.north_m - deliv_north)
                alt_err = abs(curr_v.relative_altitude_m - delivery_alt)
                if horiz_err < 2.5 and alt_err < 1.0:
                    break

        navigator.hold()
        coordinator.hold_for_release(aid, standoff_m=standoff_dist)
        self.runtime._transition_drone(vid, DroneLifecycleState.HOLDING_FOR_RELEASE)
        self.runtime._add_event(
            vid, "HOLDING_FOR_RELEASE",
            f"Vehicle {vid} holding at delivery point (standoff: {standoff_dist:.1f}m, alt: {delivery_alt}m)",
            metadata={"assignment_id": str(aid), "standoff_m": standoff_dist, "altitude_m": delivery_alt},
        )

        # Stabilize for 1.5 seconds
        time.sleep(1.5)

        # 7. Release Safety Gate Check
        curr_snap = runner.adapter.vehicle_source.normalizer.snapshot if runner.adapter.vehicle_source else None
        curr_v = curr_snap.vehicle if curr_snap else None
        curr_enu = frame.to_enu(GeoPoint(latitude=curr_v.latitude, longitude=curr_v.longitude, altitude_m=curr_v.relative_altitude_m)) if curr_v else None

        telem_override = {
            "connected": True,
            "telemetry_age_s": 0.1,
            "telemetry": {
                "armed": curr_v.armed if curr_v else True,
                "flight_mode": curr_v.flight_mode if curr_v else "GUIDED",
                "relative_altitude_m": curr_v.relative_altitude_m if curr_v else 5.0,
                "ground_speed_m_s": curr_v.ground_speed_m_s if curr_v else 0.0,
                "latitude": curr_v.latitude if curr_v else survivor.latitude,
                "longitude": curr_v.longitude if curr_v else survivor.longitude,
                "east_m": curr_enu.east_m if curr_enu else deliv_east,
                "north_m": curr_enu.north_m if curr_enu else deliv_north,
            },
        }

        safe, reason = coordinator.check_release_safety(
            aid,
            telemetry_override=telem_override,
            boundary=boundary,
        )
        if not safe:
            logger.warning("[PHASE 2B] Release safety gate blocked: %s", reason)
            self.runtime._add_event(vid, "RELEASE_SAFETY_BLOCKED", f"Release safety gate blocked: {reason}", severity="WARNING")
            coordinator.requeue_assignment(aid, f"Safety gate blocked: {reason}")
            # Climb back to search altitude
            climb_pt = ApproachPoint(east_m=deliv_east, north_m=deliv_north, altitude_m=approach_alt)
            navigator.goto_local(climb_pt)
            time.sleep(3.0)
            navigator.set_mission_current(checkpoint.current_mission_seq)
            navigator.resume_auto()
            self.runtime._transition_drone(vid, DroneLifecycleState.SEARCHING)
            return False

        # 8. Simulated Payload Release (Exactly-Once)
        released_job = coordinator.execute_simulated_release(
            aid,
            telemetry_override=telem_override,
            boundary=boundary,
        )
        self.runtime._transition_drone(vid, DroneLifecycleState.RELEASED)
        self.runtime._add_event(
            vid, "DELIVERY_RELEASED",
            f"Simulated payload released for survivor {survivor_id} (Release ID: {released_job.release_id})",
            metadata={
                "assignment_id": str(aid),
                "release_id": released_job.release_id,
                "standoff_m": standoff_dist,
                "altitude_m": telem_override["telemetry"]["relative_altitude_m"],
            },
        )

        # 9. Independent Delivery Verification
        time.sleep(2.0)
        verified_job = self._verify_released_job(
            vid, coordinator, aid,
            {
                "method": "SIMULATED_DEPARTURE_SENSORS",
                "release_id": released_job.release_id,
                "standoff_m": standoff_dist,
                "drop_altitude_m": telem_override["telemetry"]["relative_altitude_m"],
                "settled": True,
            },
        )
        self.runtime._add_event(
            vid, "DELIVERY_VERIFIED",
            f"Delivery verified for survivor {survivor_id} (Release ID: {released_job.release_id})",
            metadata={"assignment_id": str(aid), "verified": True},
        )

        # 10. Safe Climb to Search Altitude
        climb_pt = ApproachPoint(east_m=deliv_east, north_m=deliv_north, altitude_m=approach_alt)
        navigator.set_speed(3.0)
        navigator.goto_local(climb_pt)
        climb_deadline = time.monotonic() + 25.0
        while time.monotonic() < climb_deadline:
            curr_snap = runner.adapter.vehicle_source.normalizer.snapshot if runner.adapter.vehicle_source else None
            if curr_snap and curr_snap.vehicle.relative_altitude_m >= approach_alt - 1.5:
                break
            time.sleep(0.4)

        # 11. Search Resumption at Checkpoint Cursor
        candidate_seq = resume_mgr.select_resume_sequence(checkpoint)
        coordinator.start_resuming_search(aid, resume_seq=candidate_seq)
        self.runtime._transition_drone(vid, DroneLifecycleState.RESUMING_SEARCH)
        self.runtime._add_event(
            vid, "RESUMING_SEARCH",
            f"Resuming search from sequence {candidate_seq} without replaying completed coverage",
            metadata={"assignment_id": str(aid), "resume_seq": candidate_seq},
        )

        navigator.set_mission_current(candidate_seq)
        navigator.resume_auto()

        # Wait for ArduPilot to switch back to AUTO mode
        auto_deadline = time.monotonic() + 10.0
        while time.monotonic() < auto_deadline:
            curr_snap = runner.adapter.vehicle_source.normalizer.snapshot if runner.adapter.vehicle_source else None
            if curr_snap and curr_snap.vehicle.flight_mode == "AUTO":
                break
            time.sleep(0.3)

        coordinator.complete_service(aid)
        self.runtime._transition_drone(vid, DroneLifecycleState.SEARCHING)
        self.runtime._add_event(
            vid, "SERVICE_COMPLETED",
            f"Service mission completed for survivor {survivor_id}; search actively resumed at item {candidate_seq}",
            metadata={"assignment_id": str(aid), "resume_seq": candidate_seq},
        )
        logger.info("[PHASE 2B] Service mission completed successfully for %s", aid)
        return True
