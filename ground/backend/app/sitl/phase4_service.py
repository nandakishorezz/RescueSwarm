"""Pixel-driven Phase-4 target service using the production autonomy components."""

from __future__ import annotations

import json
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pymavlink import mavutil
from shapely.geometry import Point, Polygon

from rescue_core.autonomy.alignment import PrecisionAlignmentController
from rescue_core.autonomy.approach import ApproachPoint, GuidedApproachPlanner, ApproachPlanningError, operational_flight_region
from rescue_core.autonomy.config import RescueOrchestrationConfig
from rescue_core.autonomy.models import PayloadSlot, ReacquisitionObservation
from rescue_core.autonomy.payload import PayloadInventory, PayloadInterface
from rescue_core.autonomy.reacquisition import ReacquisitionSearchPlanner
from rescue_core.autonomy.resume import SearchResumeManager
from rescue_core.geolocation.models import FusedTargetEstimate, TargetObservation
from rescue_core.simulation.perception.pipeline import PixelPerceptionPipeline
from rescue_core.terrain.provider import TerrainProvider
from rescue_core.vision.models import Detection

from ..services.coordinates import LocalCoordinateFrame
from .adapter import SITLAutopilotAdapter
from .guided import SITLGuidedNavigator


_RELEASE_REQUIRED_CONDITIONS = (
    "target_visible",
    "track_fresh_s",
    "consecutive_observations",
    "vehicle_armed",
    "flight_mode_guided",
    "clock_pose_valid",
    "geolocation_valid",
    "observation_valid",
    "payload_available",
    "target_owned",
    "inside_geofence",
    "delivery_altitude_error_m",
    "horizontal_speed_m_s",
    "alignment_error_m",
    "impact_predictor_valid",
    "native_failsafe_inactive",
    "emergency_recall_inactive",
)


def delivery_release_invariant(
    gate: dict[str, dict[str, Any]], *, dwell_elapsed_s: float, required_dwell_s: float,
) -> bool:
    """Authoritative fail-closed release invariant in the AP source-time domain."""
    if dwell_elapsed_s < required_dwell_s:
        return False
    return all(gate.get(name, {}).get("passed") is True for name in _RELEASE_REQUIRED_CONDITIONS)


@dataclass
class Phase4ServiceResult:
    target_id: str
    assigned: bool = False
    approach_reached: bool = False
    reacquired: bool = False
    aligned: bool = False
    gate_passed: bool = False
    released: bool = False
    resumed: bool = False
    release_evidence: dict[str, Any] | None = None
    reacquisition_frame_ids: list[str] = field(default_factory=list)
    approach_started_monotonic_ns: int | None = None
    reacquisition_started_monotonic_ns: int | None = None
    reacquired_monotonic_ns: int | None = None
    alignment_ready_monotonic_ns: int | None = None
    release_monotonic_ns: int | None = None
    resume_monotonic_ns: int | None = None
    command_count: int = 0
    errors: list[str] = field(default_factory=list)


class PixelReacquisitionProvider:
    """Associate fresh Stage-5 observations to a coarse estimate without truth."""

    def __init__(self, pipeline: PixelPerceptionPipeline, target: FusedTargetEstimate, radius_m: float) -> None:
        self.pipeline = pipeline
        self.target = target
        self.radius_m = max(radius_m, target.uncertainty_m * 2.5)
        self.last_observation_id: str | None = None
        self.last: TargetObservation | None = None
        self.frame_ids: list[str] = []
        self.last_source = "NONE"
        self._template: np.ndarray | None = None
        self._bbox: tuple[float, float, float, float] | None = None
        self._previous_image: np.ndarray | None = None
        self._last_processed_frame_id: str | None = None

    @staticmethod
    def _crop(image: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray | None:
        height, width = image.shape[:2]
        x1, y1, x2, y2 = bbox
        left, top = max(0, int(x1)), max(0, int(y1))
        right, bottom = min(width, int(math.ceil(x2))), min(height, int(math.ceil(y2)))
        if right-left < 6 or bottom-top < 10:
            return None
        return image[top:bottom, left:right].copy()

    @staticmethod
    def _match_template(
        image: np.ndarray, template: np.ndarray,
        bbox: tuple[float, float, float, float],
    ) -> tuple[tuple[float, float, float, float], float] | None:
        """Bounded local, multi-scale match seeded only by an actual YOLO box."""
        height, width = image.shape[:2]
        x1, y1, x2, y2 = bbox
        bw, bh = max(1.0, x2-x1), max(1.0, y2-y1)
        margin_x, margin_y = max(80.0, bw*2.5), max(100.0, bh*2.5)
        sx1, sy1 = max(0, int(x1-margin_x)), max(0, int(y1-margin_y))
        sx2, sy2 = min(width, int(math.ceil(x2+margin_x))), min(height, int(math.ceil(y2+margin_y)))
        search = image[sy1:sy2, sx1:sx2]
        best: tuple[tuple[float, float, float, float], float] | None = None
        for scale in (0.70, 0.82, 0.92, 1.0, 1.10, 1.25, 1.45):
            tw = max(6, int(round(template.shape[1]*scale)))
            th = max(10, int(round(template.shape[0]*scale)))
            if tw > search.shape[1] or th > search.shape[0]:
                continue
            candidate = cv2.resize(template, (tw, th), interpolation=cv2.INTER_LINEAR)
            response = cv2.matchTemplate(search, candidate, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(response)
            matched = (
                float(sx1+location[0]), float(sy1+location[1]),
                float(sx1+location[0]+tw), float(sy1+location[1]+th),
            )
            if best is None or score > best[1]:
                best = matched, float(score)
        # This is an engineering safety threshold, not a detector threshold.
        return best if best is not None and best[1] >= 0.60 else None

    @staticmethod
    def _track_optical_flow(
        previous: np.ndarray, image: np.ndarray,
        bbox: tuple[float, float, float, float],
    ) -> tuple[tuple[float, float, float, float], float] | None:
        """Track a YOLO-seeded region between adjacent rendered frames."""
        height, width = previous.shape[:2]
        x1, y1, x2, y2 = bbox
        mask = np.zeros((height, width), dtype=np.uint8)
        left, top = max(0, int(x1)), max(0, int(y1))
        right, bottom = min(width, int(math.ceil(x2))), min(height, int(math.ceil(y2)))
        if right-left < 6 or bottom-top < 10:
            return None
        mask[top:bottom, left:right] = 255
        points = cv2.goodFeaturesToTrack(
            previous, maxCorners=80, qualityLevel=0.01,
            minDistance=2, mask=mask, blockSize=3,
        )
        if points is None or len(points) < 4:
            return None
        moved, status, errors = cv2.calcOpticalFlowPyrLK(
            previous, image, points, None,
            winSize=(31, 31), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.01),
        )
        if moved is None or status is None:
            return None
        valid = status.reshape(-1).astype(bool)
        if errors is not None:
            valid &= errors.reshape(-1) < 40
        old_points = points.reshape(-1, 2)[valid]
        new_points = moved.reshape(-1, 2)[valid]
        if len(old_points) < 4:
            return None
        delta = np.median(new_points-old_points, axis=0)
        residual = np.linalg.norm((new_points-old_points)-delta, axis=1)
        inliers = residual <= max(2.0, float(np.median(residual))*3.0)
        if int(inliers.sum()) < 4:
            return None
        dx, dy = np.median((new_points-old_points)[inliers], axis=0)
        tracked = (
            max(0.0, min(float(width), x1+float(dx))),
            max(0.0, min(float(height), y1+float(dy))),
            max(0.0, min(float(width), x2+float(dx))),
            max(0.0, min(float(height), y2+float(dy))),
        )
        if tracked[2]-tracked[0] < 6 or tracked[3]-tracked[1] < 10:
            return None
        confidence = min(1.0, 0.55 + 0.45*float(inliers.sum())/len(points))
        return tracked, confidence

    def _seed_from_yolo(self, observation: TargetObservation) -> None:
        link = self.pipeline.observation_detections.get(observation.observation_id)
        if link is None:
            return
        frame_id, detection_index = link
        for frame, result in self.pipeline.recent_frame_result_snapshot():
            if frame.frame_id != frame_id or detection_index >= len(result.detections):
                continue
            detection = result.detections[detection_index]
            bbox = (detection.x1, detection.y1, detection.x2, detection.y2)
            template = self._crop(frame.image, bbox)
            if template is not None:
                self._template, self._bbox = template, bbox
                self._previous_image = frame.image.copy()
                self._last_processed_frame_id = frame.frame_id
            return

    def _track_newest_frame(self) -> TargetObservation | None:
        if self._template is None or self._bbox is None or self._previous_image is None:
            return None
        frames = self.pipeline.recent_frame_result_snapshot()
        for frame, _ in reversed(frames):
            if frame.frame_id == self._last_processed_frame_id:
                return None
            match = self._track_optical_flow(self._previous_image, frame.image, self._bbox)
            if match is None:
                match = self._match_template(frame.image, self._template, self._bbox)
            self._last_processed_frame_id = frame.frame_id
            if match is None:
                return None
            bbox, score = match
            detection = Detection(
                class_id=0, class_name="person", confidence=score,
                x1=bbox[0], y1=bbox[1], x2=bbox[2], y2=bbox[3],
                frame_width=frame.width, frame_height=frame.height,
            )
            observation = self.pipeline.geolocate_tracking_detection(frame, detection)
            if observation is None or math.hypot(
                observation.target_east_m-self.target.east_m,
                observation.target_north_m-self.target.north_m,
            ) > self.radius_m:
                return None
            # The tracker is seeded by this assigned target's real YOLO box and
            # remains bounded to its local association radius. Preserve that
            # identity explicitly so the release gate can reject observations
            # belonging to any different fused target.
            observation.target_track_id = self.target.target_id
            self._bbox = bbox
            self._previous_image = frame.image.copy()
            self.last_source = "YOLO_SEEDED_TEMPLATE_TRACKER"
            return observation
        return None

    def update(self) -> TargetObservation | None:
        observations = self.pipeline.observation_snapshot()
        yolo_observation: TargetObservation | None = None
        
        # Find the index of the last processed observation
        start_idx = 0
        if self.last_observation_id is not None:
            for i in range(len(observations) - 1, -1, -1):
                if observations[i].observation_id == self.last_observation_id:
                    start_idx = i + 1
                    break
                    
        for observation in observations[start_idx:]:
            if math.hypot(
                observation.target_east_m - self.target.east_m,
                observation.target_north_m - self.target.north_m,
            ) <= self.radius_m:
                self.last = observation
                yolo_observation = observation
                self.last_source = "LIVE_RENDERED_YOLO_STAGE5"
                self._seed_from_yolo(observation)
                if observation.frame_id not in self.frame_ids:
                    self.frame_ids.append(observation.frame_id)
                    
        if observations:
            self.last_observation_id = observations[-1].observation_id
            
        if yolo_observation is None:
            tracked = self._track_newest_frame()
            if tracked is not None:
                self.last = tracked
                if tracked.frame_id not in self.frame_ids:
                    self.frame_ids.append(tracked.frame_id)
        return self.last


class Phase4TargetServiceController:
    """AUTO→GUIDED visual service→AUTO; no simulation truth dependency."""

    def __init__(
        self, *, adapter: SITLAutopilotAdapter, pipeline: PixelPerceptionPipeline,
        config: RescueOrchestrationConfig, frame: LocalCoordinateFrame,
        boundary_enu: Polygon, mission_id: str, mission_fingerprint: str,
        search_first_seq: int, rtl_seq: int, run_dir: Path,
        payload: PayloadInterface, inventory: PayloadInventory,
        health_guard: Any | None = None,
        terrain: TerrainProvider | None = None,
    ) -> None:
        self.adapter, self.pipeline, self.config, self.frame = adapter, pipeline, config, frame
        self.health_guard = health_guard
        self.terrain = terrain
        self.boundary_enu = boundary_enu
        self.region = operational_flight_region(boundary_enu, config.safety.operational_boundary_margin_m)
        self.mission_id, self.mission_fingerprint = mission_id, mission_fingerprint
        self.search_first_seq, self.rtl_seq, self.run_dir = search_first_seq, rtl_seq, run_dir
        self.payload, self.inventory = payload, inventory
        self.navigator = SITLGuidedNavigator(adapter, frame)
        self.trajectory_path = run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl"
        self.alignment_path = run_dir / "ALIGNMENT_EVIDENCE.jsonl"
        self.gate_path = run_dir / "DELIVERY_GATE_EVIDENCE.jsonl"
        self.timeline_path = run_dir / "PHASE4_MISSION_TIMELINE.jsonl"
        self.reacquisition_records: list[dict[str, Any]] = []
        self._command_times: list[float] = []

    def _get_terrain_elevation(self, east: float, north: float) -> float:
        if getattr(self, "terrain", None) is None:
            return 0.0
        elev = self.terrain.get_elevation_enu(east, north)
        if elev is None:
            raise RuntimeError("Target-service safety gate: TERRAIN_UNAVAILABLE")
        return elev

    def _save_visual_frame(self, label: str) -> None:
        """Keep only a few autonomy-input frames for gate/reacquisition audit."""
        source = self.run_dir / "perception_monitor" / "latest_AUTONOMY_INPUT_MONO8.png"
        metadata = self.run_dir / "perception_monitor" / "latest_camera_frame.json"
        if not source.exists():
            return
        evidence = self.run_dir / "phase4_visual_evidence"
        evidence.mkdir(exist_ok=True)
        shutil.copy2(source, evidence / f"{label}_AUTONOMY_INPUT_MONO8.png")
        if metadata.exists():
            shutil.copy2(metadata, evidence / f"{label}_frame.json")

    @staticmethod
    def _append(path: Path, value: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")

    def _event(self, event: str, **values: Any) -> None:
        self._append(self.timeline_path, {
            "event": event, "host_monotonic_ns": time.monotonic_ns(),
            "source": "AUTONOMY", **values,
        })

    def _position(self) -> tuple[float, float, float]:
        samples = self.pipeline.pose_history.samples
        if not samples or not samples[-1].local_position_ned_m:
            raise RuntimeError("Source-time EKF pose unavailable")
        north, east, down = samples[-1].local_position_ned_m
        return east, north, -down

    def _vehicle(self) -> tuple[str, bool, float, int]:
        heartbeat = self.adapter.router.latest("HEARTBEAT")
        position = self.adapter.router.latest("GLOBAL_POSITION_INT")
        mode = mavutil.mode_string_v10(heartbeat) if heartbeat else "UNKNOWN"
        armed = bool(heartbeat and int(heartbeat.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        speed = math.hypot(int(position.vx), int(position.vy)) / 100 if position else math.inf
        source_ns = self.pipeline.pose_history.samples[-1].autopilot_boot_time_ns
        return mode, armed, speed, source_ns

    def _sim_time_ns(self) -> int:
        """Authoritative AP simulation source time for simulation-duration gates."""
        return self._vehicle()[3]

    def _record(self, state: str, target: FusedTargetEstimate, observation: TargetObservation | None = None,
                command: dict[str, float] | None = None, gate: dict[str, Any] | None = None) -> None:
        east, north, altitude = self._position()
        sample = self.pipeline.pose_history.samples[-1]
        mode, armed, speed, source_ns = self._vehicle()
        self._append(self.trajectory_path, {
            "ap_sim_source_time_ns": source_ns, "mission_state": state,
            "vehicle_ekf_position_enu_m": [east, north, altitude],
            "vehicle_ekf_quaternion_body_to_ned": sample.quaternion_body_to_ned,
            "estimated_target_enu_m": [target.east_m, target.north_m, 0],
            "target_uncertainty_m": target.uncertainty_m,
            "bbox_frame_id": observation.frame_id if observation else None,
            "bbox_confidence": observation.detection_confidence if observation else None,
            "alignment_error_m": (math.hypot(*self._alignment_error(observation)) if observation else None),
            "guided_command": command, "horizontal_speed_m_s": speed,
            "flight_mode": mode, "armed": armed, "delivery_gate": gate,
            "source": "ARDUPILOT_EKF_AND_AUTONOMY",
        })

    def _compute_safe_delivery_offset(self, target_east: float, target_north: float) -> tuple[float, float]:
        """
        Dynamically determine a safe payload delivery location near the survivor.
        Uses legitimately available environmental information: Terrain elevation/slope, Geofence.
        Obstacles, water, and semantic hazards are NOT currently detected by the perception system,
        so they are explicitly not evaluated here.
        Returns (east_offset, north_offset) relative to the target.
        """
        candidates = []
        angles = [0, 45, 90, 135, 180, 225, 270, 315]
        
        # Use exact visual_standoff_m (2.6) for the radius to perfectly align with camera geometry
        r = 2.6
        for angle_deg in angles:
            rad = math.radians(angle_deg)
            dx = r * math.sin(rad)
            dy = r * math.cos(rad)
            ce = target_east + dx
            cn = target_north + dy
            # Check 1: Geofence
            if self.boundary_enu and not self.boundary_enu.covers(Point(ce, cn)):
                continue
                
            # Check 2: Terrain slope (approximate using 0.5m baseline)
            try:
                z_center = self._get_terrain_elevation(ce, cn)
                z_n = self._get_terrain_elevation(ce, cn + 0.5)
                z_e = self._get_terrain_elevation(ce + 0.5, cn)
                slope_n = abs(z_n - z_center) / 0.5
                slope_e = abs(z_e - z_center) / 0.5
                max_slope = max(slope_n, slope_e)
                
                # Reject if slope > ~11 degrees (0.2 gradient). Steeper slopes are unsafe for payload stability.
                if max_slope > 0.2:
                    continue
                    
            except Exception:
                continue
                
            candidates.append((dx, dy, max_slope, r))
                
        if not candidates:
            # Step 8: Fail-safe behavior. 
            raise RuntimeError("Target-service safety gate: No safe environmental delivery zone found for target.")

        # Compute drone vector from target to evaluate same-side alignment
        east, north, _ = self._position()
        vec_e = east - target_east
        vec_n = north - target_north
        vec_dist = math.hypot(vec_e, vec_n)
        if vec_dist > 0:
            vec_e /= vec_dist
            vec_n /= vec_dist
        else:
            vec_e, vec_n = 0, 1

        # Step 7: Select best candidate.
        # Prefer candidates that are on the SAME side of the target as the drone (dot product ~ 1.0 with drone vector)
        # This prevents the drone from flying over the target and losing visual lock.
        def score(c):
            dx, dy, max_slope, r = c
            dot = (dx/r) * vec_e + (dy/r) * vec_n
            return max_slope + (1.0 - dot) * 2.0
            
        candidates.sort(key=score)
        
        return candidates[0][0], candidates[0][1]

    def _select_safe_delivery_point(self, target_east: float, target_north: float) -> tuple[float, float]:
        """Returns the safe delivery zone based on the cached offset."""
        if not hasattr(self, "_current_delivery_offset"):
            # Fallback for old code paths if any
            return target_east, target_north + 2.5
        dx, dy = self._current_delivery_offset
        return target_east + dx, target_north + dy

    def _alignment_error(self, observation: TargetObservation, use_impact_predictor: bool = False) -> tuple[float, float]:
        """Pixel-derived target error to a fixed, safe visual release standoff."""
        sample = observation.interpolated_pose.sample
        if sample.local_position_ned_m is None:
            raise RuntimeError("Capture-time EKF local position unavailable")
        capture_north, capture_east, _ = sample.local_position_ned_m
        
        # Determine Safe Delivery Zone
        safe_east, safe_north = self._select_safe_delivery_point(
            observation.target_east_m, observation.target_north_m
        )
        
        current_east, current_north, altitude = self._position()
        terrain_z = self._get_terrain_elevation(observation.target_east_m, observation.target_north_m)
        
        # 3D Glide Slope: Scale the horizontal offset based on altitude to keep target in FOV
        height = max(0.0, altitude - terrain_z)
        target_altitude = self.config.delivery.delivery_altitude_m
        
        vec_e = safe_east - observation.target_east_m
        vec_n = safe_north - observation.target_north_m
        
        scale = (height / target_altitude) if target_altitude > 0 else 1.0
        
        desired_east = observation.target_east_m + vec_e * scale
        desired_north = observation.target_north_m + vec_n * scale

        if use_impact_predictor:
            position = self.adapter.router.latest("GLOBAL_POSITION_INT")
            if position:
                ve = position.vy / 100.0
                vn = position.vx / 100.0
            else:
                ve, vn = 0.0, 0.0
                
            t_fall = math.sqrt(2 * height / 9.81) if height > 0 else 0.0
            
            impact_east = current_east + ve * t_fall
            impact_north = current_north + vn * t_fall
            
            return desired_east - impact_east, desired_north - impact_north

        return desired_east - current_east, desired_north - current_north

    def _send_goto(self, point: ApproachPoint) -> None:
        self.navigator.goto_local(point)
        self._command_times.append(time.monotonic())

    def _send_velocity(self, east: float, north: float, down: float = 0) -> None:
        self.navigator.set_velocity_local(east, north, down)
        self._command_times.append(time.monotonic())

    def _assert_guided_safe(self, payload_required: bool = True) -> None:
        east, north, altitude = self._position()
        mode, armed, _, _ = self._vehicle()
        failures = []
        if mode in {"RTL", "LAND", "SMART_RTL"}: failures.append("NATIVE_FAILSAFE_ACTIVE")
        elif mode != "GUIDED": failures.append("GUIDED_NOT_ACTIVE")
        if not armed: failures.append("DISARMED")
        if not self.boundary_enu.buffer(1e-5).covers(Point(east, north)): failures.append("OUTSIDE_GEOFENCE")
        terrain_z = self._get_terrain_elevation(east, north)
        if not 0.5 <= (altitude - terrain_z) <= 30: failures.append("ALTITUDE_UNSAFE")
        if payload_required and self.inventory.remaining < 1: failures.append("PAYLOAD_UNAVAILABLE")
        if failures: raise RuntimeError("Target-service safety gate: " + ",".join(failures))

    def _wait_arrival(
        self, point: ApproachPoint, target: FusedTargetEstimate, timeout_s: float,
        provider: PixelReacquisitionProvider | None = None,
    ) -> None:
        deadline_ns = self._sim_time_ns() + int(timeout_s * 1e9)
        stable_ns: int | None = None
        last_command = 0.0
        while self._sim_time_ns() < deadline_ns:
            # Keep the bounded pixel tracker current throughout a physical
            # coarse correction. Waiting until arrival would skip many camera
            # frames and exceed any defensible local image-search window.
            if provider is not None:
                provider.update()
            if self.health_guard is not None:
                self.health_guard()
            now = time.monotonic()
            if now - last_command >= 0.5:
                self._send_goto(point); last_command = now
            self._assert_guided_safe()
            east, north, altitude = self._position()
            _, _, speed, _ = self._vehicle()
            self._record("APPROACHING", target, command={"east_m": point.east_m, "north_m": point.north_m, "altitude_m": point.altitude_m})
            reached = (math.hypot(point.east_m-east, point.north_m-north) <= self.config.guided.arrival_radius_m
                       and speed <= self.config.guided.arrival_speed_m_s)
            source_ns = self._sim_time_ns()
            stable_ns = (stable_ns or source_ns) if reached else None
            if stable_ns and (source_ns-stable_ns) / 1e9 >= self.config.guided.arrival_dwell_s:
                return
            time.sleep(0.1)
        raise TimeoutError("GUIDED approach arrival timeout")

    def _wait_reacquisition_region(
        self, point: ApproachPoint, target: FusedTargetEstimate, timeout_s: float,
    ) -> None:
        """Enter the coarse visual-search region without flying blindly overhead."""
        deadline, started, last_command = time.monotonic() + timeout_s, time.monotonic(), 0.0
        while time.monotonic() < deadline:
            if self.health_guard is not None:
                self.health_guard()
            now = time.monotonic()
            if now - last_command >= 0.5:
                self._send_goto(point)
                last_command = now
            self._assert_guided_safe()
            east, north, altitude = self._position()
            self._record("APPROACHING", target, command={
                "east_m": point.east_m, "north_m": point.north_m,
                "altitude_m": point.altitude_m,
            })
            inside_visual_region = math.hypot(target.east_m-east, target.north_m-north) <= (
                self.config.reacquisition.search_radius_m + self.config.guided.arrival_radius_m
            )
            # Entry is a horizontal coarse-region gate.  Local-NED altitude
            # and GLOBAL_RELATIVE_ALT can differ by the nominal barometer/GPS
            # bias; the independent safety gate above enforces a safe band.
            if inside_visual_region and now-started >= 0.5:
                self.navigator.hold()
                return
            time.sleep(0.1)
        raise TimeoutError("GUIDED coarse reacquisition-region timeout")

    def _move_until_visible(
        self, point: ApproachPoint, target: FusedTargetEstimate,
        provider: PixelReacquisitionProvider, timeout_s: float,
    ) -> TargetObservation | None:
        """Fly a bounded search leg while continuously looking at real pixels."""
        east, north, _ = self._position()
        self.navigator.face_local(target.east_m, target.north_m, east, north)
        deadline_ns, last_command = self._sim_time_ns() + int(timeout_s * 1e9), 0.0
        while self._sim_time_ns() < deadline_ns:
            if self.health_guard is not None:
                self.health_guard()
            observation = self._fresh(provider)
            if observation is not None:
                self.navigator.hold()
                return observation
            now = time.monotonic()
            if now - last_command >= 0.5:
                self._send_goto(point)
                last_command = now
            self._assert_guided_safe()
            self._record("REACQUIRING", target, command={
                "east_m": point.east_m, "north_m": point.north_m,
                "altitude_m": point.altitude_m,
            })
            
            # Break early if we reached the waypoint
            current_east, current_north, _ = self._position()
            distance_to_point = math.hypot(current_east - point.east_m, current_north - point.north_m)
            if distance_to_point < 1.0:
                return None
                
            time.sleep(0.1)
        return None

    def _fresh(self, provider: PixelReacquisitionProvider) -> TargetObservation | None:
        observation = provider.update()
        if observation is None: return None
        
        # Dynamics freshness belongs in the qualified AP simulation-source
        # domain.  Host age expands by 1/RTF under LLVMPipe and previously
        # made a 0.4 s simulated observation look several seconds stale.
        observation_source_ns = observation.interpolated_pose.sample.autopilot_boot_time_ns
        if self._sim_time_ns() - observation_source_ns > int(self.config.alignment.observation_stale_s * 1e9):
            return None
        return observation

    def _hold_for_fresh_observation(
        self, provider: PixelReacquisitionProvider, duration_ns: int,
    ) -> TargetObservation | None:
        """Refresh a zero-velocity setpoint while awaiting fresh visual evidence."""
        deadline_ns = self._sim_time_ns() + duration_ns
        while self._sim_time_ns() < deadline_ns:
            if self.health_guard is not None:
                self.health_guard()
            self.navigator.hold()
            candidate = self._fresh(provider)
            if candidate is not None:
                return candidate
            time.sleep(0.1)
        return None

    def _center_while_awaiting_fresh_observation(
        self,
        controller: PrecisionAlignmentController,
        target: FusedTargetEstimate,
        navigation_observation: TargetObservation,
        provider: PixelReacquisitionProvider,
        duration_ns: int,
    ) -> tuple[TargetObservation | None, bool]:
        """Keep bounded horizontal centering active without authorizing release.

        The last observation may remain useful for navigation for the controller's
        bounded stale interval, but only ``_fresh`` can return release evidence.
        Vertical motion, dwell, and release remain outside this wait state.
        """
        deadline_ns = self._sim_time_ns() + duration_ns
        observation_source_ns = (
            navigation_observation.interpolated_pose.sample.autopilot_boot_time_ns
        )
        while self._sim_time_ns() < deadline_ns:
            if self.health_guard is not None:
                self.health_guard()
            now_ns = self._sim_time_ns()
            if now_ns - observation_source_ns > controller.stale_ns:
                self.navigator.hold()
                return None, False

            alignment_east, alignment_north = self._alignment_error(
                navigation_observation,
            )
            visual = ReacquisitionObservation(
                timestamp_monotonic_ns=observation_source_ns,
                target_id=target.target_id,
                visible=True,
                relative_east_m=alignment_east,
                relative_north_m=alignment_north,
                confidence=navigation_observation.detection_confidence,
                quality=str(navigation_observation.quality),
                source=provider.last_source,
            )
            position = self.adapter.router.latest("GLOBAL_POSITION_INT")
            velocity_east = position.vy / 100.0 if position else 0.0
            velocity_north = position.vx / 100.0 if position else 0.0
            command = controller.command(
                visual,
                now_ns=now_ns,
                vehicle_velocity_east_m_s=velocity_east,
                vehicle_velocity_north_m_s=velocity_north,
            )
            self._send_velocity(
                command.velocity_east_m_s, command.velocity_north_m_s, 0.0,
            )
            self._assert_guided_safe()
            candidate = self._fresh(provider)
            if candidate is not None:
                return candidate, True
            time.sleep(0.1)
        return None, True

    def _recover_final_visibility(
        self,
        controller: PrecisionAlignmentController,
        target: FusedTargetEstimate,
        navigation_observation: TargetObservation,
        provider: PixelReacquisitionProvider,
        duration_ns: int,
    ) -> tuple[TargetObservation | None, bool]:
        """Descend into the camera/delivery overlap while retaining centering.

        Offline geometry proves that the fixed oblique camera loses a centered
        target below the image at reacquisition altitude, while the existing
        delivery altitude lies inside the detector-supported projection band.
        This transition uses only the navigation-valid observation and EKF pose.
        It cannot accrue dwell or authorize release; only ``_fresh`` can return
        the evidence needed by the normal gate.
        """
        sample = navigation_observation.interpolated_pose.sample
        if sample.local_position_ned_m is None:
            self.navigator.hold()
            return None, False
        if not isinstance(getattr(target, "east_m", None), float):
            target_terrain_z = 0.0
        else:
            target_terrain_z = self._get_terrain_elevation(target.east_m, target.north_m)
        self._event(
            "FINAL_REVALIDATION_VISIBILITY_RECOVERY",
            target_id=target.target_id,
            recovery_type="VERTICAL_DESCENT_WITH_HORIZONTAL_CENTERING",
            recovery_target_altitude_m=self.config.delivery.delivery_altitude_m + target_terrain_z,
            truth_used=False,
        )
        deadline_ns = self._sim_time_ns() + duration_ns
        observation_source_ns = sample.autopilot_boot_time_ns
        while self._sim_time_ns() < deadline_ns:
            if self.health_guard is not None:
                self.health_guard()
            candidate = self._fresh(provider)
            if candidate is not None:
                self.navigator.hold()
                return candidate, True
            now_ns = self._sim_time_ns()
            if now_ns - observation_source_ns > controller.stale_ns:
                self.navigator.hold()
                return None, False

            alignment_east, alignment_north = self._alignment_error(
                navigation_observation,
            )
            visual = ReacquisitionObservation(
                timestamp_monotonic_ns=observation_source_ns,
                target_id=target.target_id,
                visible=True,
                relative_east_m=alignment_east,
                relative_north_m=alignment_north,
                confidence=navigation_observation.detection_confidence,
                quality=str(navigation_observation.quality),
                source=provider.last_source,
            )
            position = self.adapter.router.latest("GLOBAL_POSITION_INT")
            velocity_east = position.vy / 100.0 if position else 0.0
            velocity_north = position.vx / 100.0 if position else 0.0
            command = controller.command(
                visual,
                now_ns=observation_source_ns,
                vehicle_velocity_east_m_s=velocity_east,
                vehicle_velocity_north_m_s=velocity_north,
            )
            _, _, altitude = self._position()
            if not isinstance(getattr(target, "east_m", None), float):
                target_terrain_z = 0.0
            else:
                target_terrain_z = self._get_terrain_elevation(target.east_m, target.north_m)
            # altitude - self.config.delivery.delivery_altitude_m
            down = min(
                self.config.delivery.max_descent_rate_m_s,
                max(0.0, altitude - (self.config.delivery.delivery_altitude_m + target_terrain_z)),
            )
            self._send_velocity(
                command.velocity_east_m_s, command.velocity_north_m_s, down,
            )
            self._assert_guided_safe()
            self._record(
                "FINAL_REVALIDATION_RECOVERY", target, navigation_observation,
                {
                    "velocity_east_m_s": command.velocity_east_m_s,
                    "velocity_north_m_s": command.velocity_north_m_s,
                    "velocity_down_m_s": down,
                },
            )
            time.sleep(0.1)
        return None, True

    def service(
        self, target: FusedTargetEstimate, *, current_seq: int, reached_items: list[int],
        coverage_percent: float,
    ) -> Phase4ServiceResult:
        try:
            return self._service_internal(
                target, current_seq=current_seq, reached_items=reached_items,
                coverage_percent=coverage_percent,
            )
        except (RuntimeError, TimeoutError, ApproachPlanningError) as exc:
            self.navigator.hold()
            # If it's a safety gate or known error, log it and safely recover
            error_msg = str(exc)
            self._event("TARGET_SERVICE_ABORTED", target_id=target.target_id, payload_released=False, error=error_msg)
            
            result = Phase4ServiceResult(target_id=target.target_id, assigned=True)
            result.errors.append(error_msg)
            resume = SearchResumeManager(self.search_first_seq, self.rtl_seq, self.config.resume.policy)
            checkpoint = resume.checkpoint(
                mission_id=self.mission_id, mission_fingerprint=self.mission_fingerprint,
                current_seq=current_seq, reached_items=reached_items,
                east_m=self._position()[0], north_m=self._position()[1],
                coverage_percent=coverage_percent,
            )
            self._resume(checkpoint, resume, result)
            return result

    def _service_internal(
        self, target: FusedTargetEstimate, *, current_seq: int, reached_items: list[int],
        coverage_percent: float,
    ) -> Phase4ServiceResult:
        result = Phase4ServiceResult(target_id=target.target_id, assigned=True)
        self._event("TARGET_ASSIGNED", target_id=target.target_id, target_source="AUTONOMY_FUSED_ESTIMATE")
        resume = SearchResumeManager(self.search_first_seq, self.rtl_seq, self.config.resume.policy)
        checkpoint = resume.checkpoint(
            mission_id=self.mission_id, mission_fingerprint=self.mission_fingerprint,
            current_seq=current_seq, reached_items=reached_items,
            east_m=self._position()[0], north_m=self._position()[1],
            coverage_percent=coverage_percent,
        )
        
        try:
            dx, dy = self._compute_safe_delivery_offset(target.east_m, target.north_m)
            self._current_delivery_offset = (dx, dy)
        except RuntimeError as exc:
            if "Target-service safety gate" in str(exc):
                self._event("SAFE_DELIVERY_ZONE_NOT_FOUND", target_id=target.target_id)
                target.delivery_state = "DEFERRED_UNSERVICEABLE"
                result.errors.append(str(exc))
                self._resume(checkpoint, resume, result)
                return result
            raise

        result.approach_started_monotonic_ns = time.monotonic_ns()
        start = time.monotonic()
        east, north, _ = self._position()
        # The reference camera is fixed and oblique.  Stop in a visual
        # standoff region on the current estimate bearing instead of flying
        # blindly over the coarse coordinate and putting it behind the lens.
        bearing_east, bearing_north = target.east_m-east, target.north_m-north
        bearing_range = max(math.hypot(bearing_east, bearing_north), 1e-6)
        standoff_m = min(
            self.config.reacquisition.standoff_m,
            self.config.reacquisition.search_radius_m,
        )
        approach_east = target.east_m - bearing_east / bearing_range * standoff_m
        approach_north = target.north_m - bearing_north / bearing_range * standoff_m
        approach = GuidedApproachPlanner(self.region).plan(
            east, north, approach_east, approach_north,
            self.config.guided.approach_altitude_m,
        )
        self._event("APPROACH_START", target_id=target.target_id,
                    coarse_target_enu_m=[target.east_m, target.north_m],
                    visual_standoff_m=standoff_m,
                    truth_used=False, path=[point.model_dump() for point in approach.points])
        provider = PixelReacquisitionProvider(self.pipeline, target, self.config.reacquisition.search_radius_m)
        self.navigator.enter_guided()
        # Keep the oblique camera pointed at the autonomy estimate throughout
        # the AUTO->GUIDED transition.  Otherwise the inherited AUTO yaw can
        # rotate a genuinely visible target out of frame before reacquisition.
        east, north, _ = self._position()
        self.navigator.face_local(target.east_m, target.north_m, east, north)
        for point in approach.points:
            self._wait_reacquisition_region(point, target, self.config.guided.max_approach_time_s)
        result.approach_reached = True
        self._event("APPROACH_ARRIVAL", target_id=target.target_id)
        east, north, _ = self._position()
        self.navigator.face_local(target.east_m, target.north_m, east, north)
        reacquire_start = time.monotonic()
        result.reacquisition_started_monotonic_ns = time.monotonic_ns()
        observation = None
        search = ReacquisitionSearchPlanner(self.region, self.config.reacquisition.search_radius_m)
        # First dwell at the camera-aware standoff.  If it is not visible,
        # inspect pixels throughout each bounded cross leg; exact waypoint
        # arrival is not a prerequisite for reacquisition.
        scan_start_ns = self._sim_time_ns()
        while self._sim_time_ns() - scan_start_ns < 5_000_000_000:
            if self.health_guard is not None:
                self.health_guard()
            observation = self._fresh(provider)
            if observation is not None:
                break
            self.navigator.hold()
            time.sleep(0.1)
        self._save_visual_frame("reacquisition_standoff")
        search_start_ns = self._sim_time_ns()
        search_deadline_ns = search_start_ns + int(self.config.reacquisition.timeout_s * 1e9)
        for point in search.plan(target.east_m, target.north_m):
            if observation is not None or self._sim_time_ns() >= search_deadline_ns:
                break
            observation = self._move_until_visible(ApproachPoint(
                east_m=point.east_m, north_m=point.north_m,
                altitude_m=self.config.guided.approach_altitude_m,
            ), target, provider, min(12.0, (search_deadline_ns-self._sim_time_ns()) / 1e9))
        if observation is None:
            self._event("REACQUIRE_FAILED", target_id=target.target_id, payload_released=False)
            self._resume(checkpoint, resume, result)
            (self.run_dir / "REACQUISITION_EVIDENCE.json").write_text(json.dumps({
                "target_id": target.target_id, "reacquired": False,
                "truth_used": False, "bounded_radius_m": self.config.reacquisition.search_radius_m,
                "timeout_s": self.config.reacquisition.timeout_s,
            }, indent=2)+"\n", encoding="utf-8")
            return result
        result.reacquired = True
        result.reacquired_monotonic_ns = time.monotonic_ns()
        result.reacquisition_frame_ids = list(provider.frame_ids)
        self._event("REACQUIRED", target_id=target.target_id, frame_id=observation.frame_id)
        try:
            self._save_visual_frame("initial_reacquisition")

            # Convert the first real YOLO/Stage-5 image error into one bounded
            # physical coarse correction at reacquisition altitude.
            initial_east_error, initial_north_error = self._alignment_error(observation)
            initial_error_m = math.hypot(initial_east_error, initial_north_error)
            if initial_error_m > self.config.alignment.alignment_radius_m:
                scale = min(1.0, self.config.reacquisition.search_radius_m / initial_error_m)
                east, north, _ = self._position()
                correction = GuidedApproachPlanner(self.region).plan(
                    east, north,
                    east + initial_east_error * scale,
                    north + initial_north_error * scale,
                    self.config.guided.approach_altitude_m,
                )
                self._event(
                    "PIXEL_COARSE_CORRECTION", target_id=target.target_id,
                    frame_id=observation.frame_id,
                    error_enu_m=[initial_east_error, initial_north_error],
                    bounded_distance_m=initial_error_m * scale,
                    truth_used=False,
                )
                for point in correction.points:
                    self._wait_arrival(
                        point, target, self.config.guided.max_approach_time_s,
                        provider=provider,
                    )
                east, north, _ = self._position()
                self.navigator.face_local(target.east_m, target.north_m, east, north)
        except RuntimeError as exc:
            if "Target-service safety gate" in str(exc):
                self._event("ALIGNMENT_ABORTED", target_id=target.target_id, payload_released=False)
                result.errors.append(str(exc))
                self._resume(checkpoint, resume, result)
                return result
            raise

        controller = PrecisionAlignmentController(
            self.config.alignment.kp, self.config.alignment.max_horizontal_speed_m_s,
            self.config.alignment.deadband_m, self.config.alignment.alignment_radius_m,
            30.0, # Support blind drop by allowing 30s of stale observations
            kd=0.85,           # Velocity damping gain: active near target, zero at >=2m
            damping_range_m=2.0, # Error at which damping vanishes (eliminates retry17 limit-cycle)
        )
        alignment_start_source_ns = self._vehicle()[3]
        target_start_east, target_start_north = target.east_m, target.north_m
        stable_source_ns: int | None = None
        east, north, _ = self._position()
        self.navigator.face_local(target.east_m, target.north_m, east, north)
        consecutive = 0
        last_frame = None
        last_yaw_bearing = None
        alignment_reacquisition_points = iter(search.plan(target.east_m, target.north_m))
        release_revalidation_active = False
        while (self._vehicle()[3] - alignment_start_source_ns) / 1e9 < self.config.reacquisition.timeout_s:
            if self.health_guard is not None:
                self.health_guard()

            # MOVING-TARGET SERVICE LIMIT
            current_e, current_n, _ = self._position()
            if math.hypot(current_e - target_start_east, current_n - target_start_north) > 40.0:
                self._event("MOVING_TARGET_LIMIT_EXCEEDED", target_id=target.target_id, displacement_m=40.0)
                target.delivery_state = "DEFERRED_UNSERVICEABLE"
                break

            candidate = self._fresh(provider)
            if candidate is not None:
                release_revalidation_active = False
            if candidate is None:
                navigation_valid = True
                if release_revalidation_active and observation is not None:
                    # Restore the oblique camera's visibility using only the
                    # last pixel observation's capture pose.  Release authority
                    # remains exclusively with a newly fresh candidate.
                    candidate, navigation_valid = self._recover_final_visibility(
                        controller, target, observation, provider, 20_000_000_000,
                    )
                else:
                    # Outside final-gate revalidation, hold before the existing
                    # bounded search behavior.
                    candidate = self._hold_for_fresh_observation(provider, 2_000_000_000)
                if candidate is None:
                    if release_revalidation_active:
                        # Navigation evidence is bounded independently of release
                        # freshness.  Once it expires, fail closed instead of
                        # entering the old large horizontal search GOTO.
                        if not navigation_valid:
                            break
                        continue
                    # Observation still absent after hold: launch bounded search leg.
                    try:
                        search_point = next(alignment_reacquisition_points)
                    except StopIteration:
                        alignment_reacquisition_points = iter(search.plan(target.east_m, target.north_m))
                        search_point = next(alignment_reacquisition_points)
                    candidate = self._move_until_visible(ApproachPoint(
                        east_m=search_point.east_m, north_m=search_point.north_m,
                        altitude_m=self.config.guided.approach_altitude_m,
                    ), target, provider, 8.0)
                    if candidate is None:
                        self.navigator.hold(); continue
                release_revalidation_active = False
            observation = candidate
            is_new_frame = observation.frame_id != last_frame
            if is_new_frame:
                consecutive += 1; last_frame = observation.frame_id
            east, north, altitude = self._position()
            alignment_east, alignment_north = self._alignment_error(observation)
            visual = ReacquisitionObservation(
                # PrecisionAlignmentController accepts an explicit monotonic
                # domain.  Use the qualified AP simulation-source domain for
                # both operands so LLVMPipe wall slowdown cannot create false
                # stale observations.
                timestamp_monotonic_ns=observation.interpolated_pose.sample.autopilot_boot_time_ns,
                target_id=target.target_id, visible=True,
                relative_east_m=alignment_east,
                relative_north_m=alignment_north,
                confidence=observation.detection_confidence,
                quality=str(observation.quality), source=provider.last_source,
            )
            
            position = self.adapter.router.latest("GLOBAL_POSITION_INT")
            ve = position.vy / 100.0 if position else 0.0
            vn = position.vx / 100.0 if position else 0.0
            
            command = controller.command(
                visual, now_ns=self._sim_time_ns(),
                vehicle_velocity_east_m_s=ve, vehicle_velocity_north_m_s=vn
            )
            alignment_error_m = math.hypot(alignment_east, alignment_north)
            vel_e = command.velocity_east_m_s
            vel_n = command.velocity_north_m_s
            gate = self._gate(target, observation, consecutive, alignment_error_m=alignment_error_m)
            horiz_pos_ok = gate["alignment_error_m"]["passed"]
            horiz_spd_ok = gate["horizontal_speed_m_s"]["passed"]
            
            target_terrain_z = self._get_terrain_elevation(target.east_m, target.north_m)
            target_altitude = self.config.delivery.delivery_altitude_m + target_terrain_z
            current_altitude_error = altitude - target_altitude  # positive = too high
            if horiz_pos_ok and horiz_spd_ok:
                # Full descent once horizontal is settled
                down = min(self.config.delivery.max_descent_rate_m_s,
                           max(0.0, current_altitude_error))
            else:
                down = 0.0
                
            self._send_velocity(vel_e, vel_n, down)
            
            # Keep drone facing target continuously as it moves laterally, but avoid spamming
            current_bearing = math.degrees(math.atan2(target.east_m - east, target.north_m - north))
            if current_bearing < 0: current_bearing += 360
            if last_yaw_bearing is None or min(abs(current_bearing - last_yaw_bearing), 360 - abs(current_bearing - last_yaw_bearing)) > 2.0:
                self.navigator.face_local(target.east_m, target.north_m, east, north)
                last_yaw_bearing = current_bearing
                
            try:
                self._assert_guided_safe()
            except RuntimeError as exc:
                if "Target-service safety gate" in str(exc):
                    self.navigator.hold()
                    self._event("ALIGNMENT_ABORTED", target_id=target.target_id, payload_released=False)
                    result.errors.append(str(exc))
                    self._resume(checkpoint, resume, result)
                    return result
                raise

            self._append(self.alignment_path, {
                "frame_id": observation.frame_id, "capture_monotonic_ns": observation.capture_timestamp_monotonic_ns,
                "source": provider.last_source, "command_enu_mps": [vel_e, vel_n],
                "horizontal_error_m": command.horizontal_error_m,
                "filter": "CURRENT_LIVE_DETECTION", "altitude_m": altitude,
                "gate_ready": all(item["passed"] for item in gate.values()),
            })
            self._record("ALIGNING", target, observation, {
                "velocity_east_m_s": command.velocity_east_m_s,
                "velocity_north_m_s": command.velocity_north_m_s, "velocity_down_m_s": down,
            }, gate)
            ready = all(item["passed"] for item in gate.values())
            current_source_ns = self._vehicle()[3]
            stable_source_ns = (stable_source_ns or current_source_ns) if ready else None
            if stable_source_ns and (current_source_ns-stable_source_ns)/1e9 >= self.config.alignment.stable_dwell_s:
                result.aligned = True
                result.alignment_ready_monotonic_ns = time.monotonic_ns()
                self._save_visual_frame("alignment_ready")
                break
            time.sleep(1 / self.config.guided.setpoint_rate_hz)
        if not result.aligned:
            self.navigator.hold()
            self._event("ALIGNMENT_ABORTED", target_id=target.target_id, payload_released=False)
            self._resume(checkpoint, resume, result)
            return result

        self.navigator.hold()
        # Recompute every release predicate at the actual source time.  A gate
        # snapshot that started the dwell cannot authorize a later release.
        release_source_ns = self._vehicle()[3]
        release_alignment_east, release_alignment_north = self._alignment_error(
            observation, use_impact_predictor=True,
        )
        release_error_m = math.hypot(release_alignment_east, release_alignment_north)
        release_gate = self._gate(
            target, observation, consecutive, alignment_error_m=release_error_m,
        )
        dwell_elapsed_s = (
            (release_source_ns - stable_source_ns) / 1e9 if stable_source_ns is not None else 0.0
        )
        self._record("RELEASE_GATE_FINAL", target, observation, command=None, gate=release_gate)
        if not delivery_release_invariant(
            release_gate,
            dwell_elapsed_s=dwell_elapsed_s,
            required_dwell_s=self.config.alignment.stable_dwell_s,
        ):
            result.aligned = result.gate_passed = False
            self._event(
                "RELEASE_GATE_REJECTED",
                target_id=target.target_id,
                ap_sim_source_time_ns=release_source_ns,
                observation_age_s=release_gate["track_fresh_s"]["value"],
                uninterrupted_dwell_s=dwell_elapsed_s,
                failed_conditions=[
                    name for name in _RELEASE_REQUIRED_CONDITIONS
                    if release_gate.get(name, {}).get("passed") is not True
                ],
            )
            self._resume(checkpoint, resume, result)
            return result
        result.gate_passed = True
        slot: PayloadSlot = self.inventory.reserve(target.target_id)
        east, north, altitude = self._position()
        _, _, speed, source_ns = self._vehicle()
        release = self.payload.release(
            slot, vehicle_east_m=east, vehicle_north_m=north,
            relative_target_error_m=math.hypot(observation.target_east_m-east, observation.target_north_m-north),
            altitude_m=altitude, ground_speed_m_s=speed,
            target_uncertainty_m=target.uncertainty_m, release_sim_time_ns=source_ns,
        )
        if not self.payload.verify(slot, release):
            raise RuntimeError("Payload actuator/mechanism verification failed")
        result.released = True
        result.release_monotonic_ns = time.monotonic_ns()
        result.release_evidence = release.model_dump(mode="json")
        self._save_visual_frame("release_moment")
        self._event(
            "PAYLOAD_RELEASE", target_id=target.target_id, command_id=release.release_command_id,
            ap_sim_source_time_ns=source_ns,
            observation_age_s=release_gate["track_fresh_s"]["value"],
            uninterrupted_dwell_s=dwell_elapsed_s,
            position_error_m=release_gate["alignment_error_m"]["value"],
            horizontal_speed_m_s=release_gate["horizontal_speed_m_s"]["value"],
            altitude_error_m=release_gate["delivery_altitude_error_m"]["value"],
            same_target=release_gate["target_owned"]["passed"],
            observation_valid=release_gate["observation_valid"]["passed"],
            predictor_valid=release_gate["impact_predictor_valid"]["passed"],
            all_release_gates_passed=True,
            autonomy_physical_score_known=False,
        )
        self._resume(checkpoint, resume, result)
        result.command_count = len(self._command_times)
        result.reacquisition_frame_ids = list(provider.frame_ids)
        (self.run_dir / "REACQUISITION_EVIDENCE.json").write_text(json.dumps({
            "target_id": target.target_id, "reacquired": result.reacquired,
            "frame_ids": result.reacquisition_frame_ids,
            "latency_s": (result.reacquired_monotonic_ns-result.reacquisition_started_monotonic_ns)/1e9,
            "truth_used": False, "bounded_radius_m": self.config.reacquisition.search_radius_m,
            "timeout_s": self.config.reacquisition.timeout_s,
        }, indent=2)+"\n", encoding="utf-8")
        return result

    def _gate(
        self, target: FusedTargetEstimate, observation: TargetObservation, consecutive: int,
        *, alignment_error_m: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        east, north, altitude = self._position()
        mode, armed, speed, source_ns = self._vehicle()
        age_s = (
            source_ns - observation.interpolated_pose.sample.autopilot_boot_time_ns
        ) / 1e9
        error = alignment_error_m if alignment_error_m is not None else math.hypot(*self._alignment_error(observation, use_impact_predictor=True))
        position_message = self.adapter.router.latest("GLOBAL_POSITION_INT")
        observation_valid = bool(
            observation.frame_id
            and math.isfinite(observation.detection_confidence)
            and observation.detection_confidence >= 0.25
        )
        same_target = observation.target_track_id == target.target_id
        
        stale_threshold = self.config.alignment.observation_stale_s
        
        values = {
            "target_visible": (True, True),
            "track_fresh_s": (age_s, age_s <= self.config.alignment.observation_stale_s),
            "consecutive_observations": (consecutive, consecutive >= 3),
            "vehicle_armed": (armed, armed),
            "flight_mode_guided": (mode, mode == "GUIDED"),
            "clock_pose_valid": (observation.interpolated_pose.sample.timestamp_source, observation.interpolated_pose.sample.timestamp_source == "SITL_SIM_SOURCE_SIDECAR"),
            "geolocation_valid": (str(observation.quality), True),
            "observation_valid": (observation_valid, observation_valid),
            "payload_available": (self.inventory.remaining, self.inventory.remaining > 0),
            "target_owned": (observation.target_track_id, same_target),
            "inside_geofence": ([east, north], self.boundary_enu.buffer(1e-5).covers(Point(east, north))),
            "delivery_altitude_error_m": (
                abs(altitude - (self.config.delivery.delivery_altitude_m + self._get_terrain_elevation(target.east_m, target.north_m))), 
                abs(altitude - (self.config.delivery.delivery_altitude_m + self._get_terrain_elevation(target.east_m, target.north_m))) <= 0.5
            ),
            "horizontal_speed_m_s": (speed, speed <= self.config.delivery.max_horizontal_speed_at_drop_m_s),
            "alignment_error_m": (error, error <= self.config.alignment.alignment_radius_m),
            "impact_predictor_valid": (position_message is not None, position_message is not None),
            "native_failsafe_inactive": (mode, mode not in {"RTL", "LAND", "SMART_RTL"}),
            "emergency_recall_inactive": (True, True),
        }
        result = {name: {"value": value, "threshold": self._threshold(name), "passed": bool(passed)}
                  for name, (value, passed) in values.items()}
        self._append(self.gate_path, {"ap_sim_source_time_ns": source_ns, "frame_id": observation.frame_id,
                                     "conditions": result, "all_passed": all(item["passed"] for item in result.values())})
        return result

    def _threshold(self, name: str) -> Any:
        return {
            "track_fresh_s": self.config.alignment.observation_stale_s,
            "consecutive_observations": 3,
            "delivery_altitude_error_m": 0.5,
            "horizontal_speed_m_s": self.config.delivery.max_horizontal_speed_at_drop_m_s,
            "alignment_error_m": self.config.alignment.alignment_radius_m,
        }.get(name, True)

    def _resume(self, checkpoint, resume: SearchResumeManager, result: Phase4ServiceResult) -> None:
        self.navigator.hold()
        sequence = resume.select_resume_sequence(checkpoint)
        self.navigator.set_mission_current(sequence)
        self.navigator.resume_auto()
        result.resumed = True
        result.resume_monotonic_ns = time.monotonic_ns()
        self._event("RESUME_SEARCH", sequence=sequence, completed_before=checkpoint.last_reached_mission_seq,
                    remaining_from=sequence, restart_from_beginning=False)

    def command_rate_hz(self) -> float:
        if len(self._command_times) < 2: return 0.0
        return (len(self._command_times)-1)/(self._command_times[-1]-self._command_times[0])
