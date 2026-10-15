"""Live camera-detection to persistent survivor observation adapter.

This module intentionally consumes only detector boxes, configured simulation
camera geometry, and MAVLink-derived vehicle estimates.  It never imports a
scenario survivor, Gazebo entity pose, or evaluator/truth API.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any

from rescue_core.geolocation.fusion import SpatiotemporalTargetTracker, TargetFusionEngine
from rescue_core.geolocation.geolocator import MissionRegionValidator, TargetGeolocator
from rescue_core.geolocation.ground import FlatGroundModel
from rescue_core.geolocation.models import FusedTargetEstimate, RejectedObservation, TargetObservation
from rescue_core.geolocation.pose import PoseHistory, PoseSample
from rescue_core.geolocation.transforms import enu_to_ned, quaternion_from_euler
from rescue_core.geolocation.uncertainty import GeolocationUncertaintyEstimator
from rescue_core.simulation.perception.pipeline import simulation_camera_geometry
from rescue_core.simulation.profiles import CameraProfile
from rescue_core.vision.models import Detection, DetectionResult

from ..domain.mission import GeoPoint
from ..domain.vehicle import VehicleState
from ..services.coordinates import EnuPoint, LocalCoordinateFrame


@dataclass(frozen=True)
class LiveGeolocationResult:
    observation: TargetObservation | None
    estimate: FusedTargetEstimate | None
    reason: str | None = None


class LiveSurvivorGeolocator:
    """Mission-scoped, pixel-only survivor projection and association.

    The live bridge has no Gazebo-to-MAVLink clock witness yet, so camera
    receipt time is explicitly matched to a bounded MAVLink pose history.  The
    resulting uncertainty therefore remains an approximate simulation estimate.
    """

    # Before takeoff, a pixel ray intersects the configured flat ground within
    # centimetres of the camera.  That is a geometrically valid intersection
    # but not a valid aerial survivor location, and it previously let several
    # background boxes create competing candidate tracks at the launch point.
    MINIMUM_AERIAL_PROJECTION_RANGE_M = 2.0
    # In the accepted 8 m / 1280x800 Gazebo search, real person boxes occupy
    # a small fraction of the frame.  Live false boxes spanning 719-800 rows
    # projected 10 m away and confirmed a second ID for that same person.
    # Keep those boxes in the camera overlay, but do not use their centers as
    # independent ground-location/identity evidence.
    MAXIMUM_PERSON_BBOX_HEIGHT_FRACTION = 0.65

    def __init__(
        self,
        mission_id: str,
        origin: GeoPoint,
        camera: CameraProfile,
        vehicle_id: str = "drone_01",
        mission_region_validator: MissionRegionValidator | None = None,
    ):
        self.mission_id = mission_id
        self.vehicle_id = vehicle_id
        self.frame = LocalCoordinateFrame(origin)
        self.pose_history = PoseHistory(history_seconds=30, max_extrapolation_ms=500)
        calibration, extrinsics = simulation_camera_geometry(camera)
        self.camera = camera
        self.geolocator = TargetGeolocator(
            calibration=calibration,
            extrinsics=extrinsics,
            pose_history=self.pose_history,
            clock_sync=None,
            ground_model=FlatGroundModel(0.0, "SIMULATION_FLAT_GROUND"),
            wgs84_converter=lambda east, north, up: self.frame.enu_to_wgs84(east, north, up),
            uncertainty=GeolocationUncertaintyEstimator(
                horizontal_position_prior_m=1.5,
                vertical_position_prior_m=1.0,
                pixel_prior_px=2.0,
                attitude_prior_deg=0.5,
                mount_prior_deg=0.25,
                minimum_reported_uncertainty_m=0.75,
            ),
            minimum_gps_fix=3,
            maximum_view_angle_from_nadir_deg=70,
            require_sync=False,
            require_calibrated_extrinsics=False,
            mission_region_validator=mission_region_validator,
        )
        # A confirmed track is retained for the full short mission rather than
        # being discarded after a brief occlusion.  Same-frame boxes can never
        # associate with one another (enforced by the shared tracker).
        self.tracker = SpatiotemporalTargetTracker(
            mission_id=mission_id,
            fusion=TargetFusionEngine(),
            wgs84_converter=lambda east, north, up: self.frame.enu_to_wgs84(east, north, up),
            minimum_observations=3,
            minimum_time_span_s=0.15,
            association_radius_m=16.0,
            confirmed_reacquisition_radius_m=18.0,
            time_window_s=60.0,
            minimum_detection_confidence=0.40,
            short_term_pixel_radius_px=45.0,
        )
        self._lock = threading.Lock()

    def update_vehicle_pose(self, vehicle: VehicleState, monotonic_time_ns: int | None = None) -> None:
        """Add one MAVLink-normalized pose without accessing simulation truth."""
        if not vehicle.connected or vehicle.gps_fix_type < 3:
            return
        point = self.frame.to_enu(GeoPoint(
            latitude=vehicle.latitude,
            longitude=vehicle.longitude,
            altitude_m=vehicle.absolute_altitude_m,
        ))
        # Relative altitude is the simulation-ground height; it is more useful
        # than an MSL value for the explicitly flat simulation ground model.
        up_m = vehicle.relative_altitude_m
        quaternion = vehicle.quaternion_body_to_ned
        if quaternion is None:
            quaternion = tuple(float(value) for value in quaternion_from_euler(
                math.radians(vehicle.roll_deg), math.radians(vehicle.pitch_deg), math.radians(vehicle.yaw_deg),
            ))
        now_monotonic_ns = monotonic_time_ns if monotonic_time_ns is not None else time.monotonic_ns()
        sample = PoseSample(
            vehicle_id=vehicle.vehicle_id,
            autopilot_boot_time_ns=now_monotonic_ns,
            host_monotonic_time_ns=now_monotonic_ns,
            host_wall_time_ns=time.time_ns(),
            latitude=vehicle.latitude,
            longitude=vehicle.longitude,
            absolute_altitude_m=vehicle.absolute_altitude_m,
            relative_altitude_m=vehicle.relative_altitude_m,
            local_position_ned_m=tuple(float(value) for value in enu_to_ned((point.east_m, point.north_m, up_m))),
            quaternion_body_to_ned=quaternion,
            roll_rad=math.radians(vehicle.roll_deg),
            pitch_rad=math.radians(vehicle.pitch_deg),
            yaw_rad=math.radians(vehicle.yaw_deg),
            velocity_ned_m_s=vehicle.velocity_ned_m_s,
            gps_fix=vehicle.gps_fix_type,
            satellites=vehicle.satellites,
            horizontal_accuracy_m=1.5,
            vertical_accuracy_m=1.0,
            accuracy_sources={"horizontal": "SIMULATION_CONFIGURED", "vertical": "SIMULATION_CONFIGURED"},
            source_message_ids=[f"vehicle:{vehicle.sequence}"],
        )
        with self._lock:
            self.pose_history.add(sample)

    @staticmethod
    def _suppress_duplicate_detections(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Class-aware duplicate suppression for multiple overlapping boxes in a single frame."""
        person_detections = [d for d in detections if int(d.get("class_id", -1)) == 0]
        other_detections = [d for d in detections if int(d.get("class_id", -1)) != 0]
        if len(person_detections) <= 1:
            return detections

        sorted_dets = sorted(person_detections, key=lambda d: float(d.get("conf", 0)), reverse=True)
        kept: list[dict[str, Any]] = []

        for cand in sorted_dets:
            cx, cy, cw, ch = (float(v) for v in cand["bbox"])
            c_area = cw * ch
            c_center = (cx + cw / 2.0, cy + ch / 2.0)
            c_diag = math.hypot(cw, ch)
            is_dup = False

            for existing in kept:
                ex, ey, ew, eh = (float(v) for v in existing["bbox"])
                e_area = ew * eh
                e_center = (ex + ew / 2.0, ey + eh / 2.0)
                e_diag = math.hypot(ew, eh)

                ix1 = max(cx, ex)
                iy1 = max(cy, ey)
                ix2 = min(cx + cw, ex + ew)
                iy2 = min(cy + ch, ey + eh)
                inter_w = max(0.0, ix2 - ix1)
                inter_h = max(0.0, iy2 - iy1)
                intersection = inter_w * inter_h

                union = c_area + e_area - intersection
                iou = intersection / union if union > 0 else 0.0
                containment = intersection / min(c_area, e_area) if min(c_area, e_area) > 0 else 0.0
                center_dist = math.hypot(c_center[0] - e_center[0], c_center[1] - e_center[1])

                if (
                    iou >= 0.35
                    or containment >= 0.50
                    or (center_dist <= 0.6 * max(c_diag, e_diag) and containment >= 0.30)
                ):
                    is_dup = True
                    break

            if not is_dup:
                kept.append(cand)

        return kept + other_detections

    def observe(
        self, *, frame_id: str, timestamp_monotonic_ns: int, detections: list[dict[str, Any]],
        width: int, height: int,
    ) -> list[LiveGeolocationResult]:
        """Project class-0 boxes from this exact camera frame and associate them."""
        outcomes: list[LiveGeolocationResult] = []
        frame_track_ids: set[str] = set()
        clean_detections = self._suppress_duplicate_detections(detections)
        for item in clean_detections:
            if int(item.get("class_id", -1)) != 0:
                continue
            x, y, box_width, box_height = (float(value) for value in item["bbox"])
            if box_height > self.MAXIMUM_PERSON_BBOX_HEIGHT_FRACTION * height:
                outcomes.append(LiveGeolocationResult(
                    None, None, "DETECTION_BOX_TOO_TALL_FOR_GEOLOCATION",
                ))
                continue
            detection = Detection(
                class_id=0, class_name=str(item.get("class", "item")), confidence=float(item["conf"]),
                x1=max(0.0, x), y1=max(0.0, y),
                x2=min(float(width), x + box_width), y2=min(float(height), y + box_height),
                frame_width=width, frame_height=height,
            )
            result = DetectionResult(
                frame_id=frame_id, source_id="gazebo_camera", capture_timestamp_monotonic_ns=timestamp_monotonic_ns,
                capture_timestamp_wall_ns=time.time_ns(), inference_started_ns=timestamp_monotonic_ns,
                inference_finished_ns=max(timestamp_monotonic_ns, time.monotonic_ns()),
                model_id="stage13sim_nadir_person_v3/best.pt", backend="pytorch",
                original_channel_format="L_INT8", model_input_format="RGB", detections=[detection],
                preprocess_ms=0.0, inference_ms=0.0, postprocess_ms=0.0, end_to_end_ms=0.0,
            )
            with self._lock:
                projected = self.geolocator.geolocate(
                    result, detection, self.vehicle_id, active_width=width, active_height=height,
                    active_sensor_mode="MONO8_SIMULATION", evidence_references=[frame_id],
                )
                if isinstance(projected, RejectedObservation):
                    print(f"[GEOLOCATE REJECTED] {frame_id}: {projected.reason}", flush=True)
                    outcomes.append(LiveGeolocationResult(None, None, projected.reason))
                    continue
                print(f"[GEOLOCATE ACCEPTED] {frame_id}: E={projected.target_east_m:.2f}, N={projected.target_north_m:.2f}, unc={projected.position_uncertainty_m:.2f}m", flush=True)
                if projected.range_m < self.MINIMUM_AERIAL_PROJECTION_RANGE_M:
                    outcomes.append(LiveGeolocationResult(
                        None, None, "ALTITUDE_TOO_LOW_FOR_GEOLOCATION",
                    ))
                    continue
                estimate = self.tracker.process(projected, excluded_track_ids=frame_track_ids)
                if projected.target_track_id is not None:
                    canonical_id = self.tracker.canonical_id(projected.target_track_id)
                    projected.target_track_id = canonical_id
                    frame_track_ids.add(canonical_id)
            outcomes.append(LiveGeolocationResult(projected, estimate))
        return outcomes

    def pop_merged_events(self) -> list[tuple[str, str]]:
        """Return and clear any target ID merges (superseded_id, canonical_id)."""
        with self._lock:
            events = list(self.tracker.merged_events)
            self.tracker.merged_events.clear()
            return events

