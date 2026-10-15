import asyncio
import struct
import time
import socket
import subprocess
import shlex
from uuid import uuid4
import cv2
import numpy as np
import base64
from pathlib import Path
from ultralytics import YOLO
from ..services.telemetry_service import RealtimeEnvelope
from .live_survivor_geolocation import LiveSurvivorGeolocator

# This is deliberately scoped to the accepted V3 checkpoint.  The checkpoint
# embeds ``item``, while its audited dataset class 0 is explicitly ``person``.
# Other models retain their own emitted labels.
ACTIVE_PERSON_CHECKPOINT = Path("data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt")
ACTIVE_DETECTION_CONFIDENCE = 0.25
# V3 training, checkpoint selection, original holdout, and mission-negative
# acceptance all use 640.  A live 1280 experiment detected undersized 30 m
# actors but also produced persistent terrain false positives.  Runtime keeps
# the accepted resolution; mission altitude supplies the validated pixel scale.
ACTIVE_INFERENCE_SIZE = 640


def detection_display_label(model_path: Path, class_id: int, model_label: str) -> str:
    if model_path.as_posix().endswith(ACTIVE_PERSON_CHECKPOINT.as_posix()) and class_id == 0:
        return "person"
    return model_label


def accepts_active_detection(confidence: float) -> bool:
    """Keep live filtering aligned with the accepted V3 PyTorch evaluation."""
    return confidence >= ACTIVE_DETECTION_CONFIDENCE


def brightness_statistics(image: np.ndarray) -> dict[str, float | int]:
    """Return compact luma evidence without changing inference pixels."""
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    else:
        gray = image
    return {
        "min": int(gray.min()),
        "p01": float(np.percentile(gray, 1)),
        "median": float(np.percentile(gray, 50)),
        "p99": float(np.percentile(gray, 99)),
        "max": int(gray.max()),
        "mean": float(gray.mean()),
        "stddev": float(gray.std()),
    }


def is_persistent_survivor_estimate(estimate, mission_id: str) -> bool:
    """Candidates are tracker-internal; only confirmed current-mission tracks register."""
    return (
        estimate is not None
        and estimate.mission_id == mission_id
        and estimate.state.value == "CONFIRMED"
    )


def allows_survivor_registration(execution_state) -> bool:
    """Only search-flight pixels may create persistent survivor evidence.

    Camera streaming and inference intentionally continue during takeoff and
    RTL, but propeller/airframe views from those phases are not search
    observations.  The runner's real execution state is the authority; no
    scenario truth is consulted here.
    """
    return getattr(execution_state, "value", execution_state) == "SEARCHING"


class SurveillanceService:
    def __init__(
        self, root_dir: Path, websocket_manager, port: int = 45678, camera_topic: str | None = None,
        *, mission_origin=None, camera_profile=None, geolocation_service=None,
        vehicle_id: str = "drone_01", precise_helper_stop: bool = False,
        observation_callback=None,
        model_override=None, inference_lock=None,
        mission_region_validator=None,
    ):
        self.root_dir = root_dir
        self.websocket_manager = websocket_manager
        self.port = port
        self.vehicle_id = vehicle_id
        self.precise_helper_stop = precise_helper_stop
        self.observation_callback = observation_callback
        self.model_override = model_override
        self.inference_lock = inference_lock
        self.mission_region_validator = mission_region_validator
        self.helper_pid_file = f"/tmp/rescueswarm_camera_{uuid4().hex}.pid" if precise_helper_stop else None
        self.camera_topic = camera_topic or "/drone_01/ov9281_reference_simulation/image"
        self.model = None
        self.model_path: Path | None = None
        self.mission_origin = mission_origin
        self.camera_profile = camera_profile
        self.geolocation_service = geolocation_service
        self.survivor_locator: LiveSurvivorGeolocator | None = None
        self._target_publish_at: dict[str, float] = {}
        self.model_error: str | None = None
        self.server = None
        self.wsl_process = None
        self.is_running = False
        self._task = None
        self.frame_count = 0
        self.decoded_frame_count = 0
        self.inference_count = 0
        self.last_frame_at: float | None = None
        self.latest_error: str | None = None
        self.latest_brightness: dict[str, object] | None = None
        self.registration_enabled = False
        self._is_inferring = False

    async def start(self, mission_id: str):
        self.is_running = True
        self._is_inferring = False
        self.frame_count = 0
        self.decoded_frame_count = 0
        self.inference_count = 0
        self.last_frame_at = None
        self.latest_error = None
        self.latest_brightness = None
        self._target_publish_at = {}
        self.registration_enabled = False
        self.server = await asyncio.start_server(self.handle_client, '0.0.0.0', self.port)
        model_path = self.root_dir / ACTIVE_PERSON_CHECKPOINT
        self.model_path = model_path
        try:
            self.model = self.model_override if self.model_override is not None else YOLO(str(model_path))
            print(f"[Surveillance] Model loaded: {model_path} classes={self.model.names}")
        except Exception as exc:
            self.model = None
            self.model_error = f"model load failed: {exc}"
            print(f"[Surveillance] {self.model_error}")
        
        script_path = self.root_dir / "ground/backend/app/sitl/camera_stream_helper.py"
        wsl_path = str(script_path).replace('\\', '/')
        # convert C:/... to /mnt/c/...
        if wsl_path[1] == ':':
            wsl_path = "/mnt/" + wsl_path[0].lower() + wsl_path[2:]
            
        print(f"[Surveillance] Starting WSL camera bridge at {wsl_path} on port {self.port} for mission {mission_id}")
        helper_command = f"python3 {shlex.quote(wsl_path)} {self.port} {shlex.quote(self.camera_topic)}"
        if self.helper_pid_file:
            helper_command = f"echo $$ > {self.helper_pid_file}; exec {helper_command}"
        cmd = ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c", helper_command]
        self.wsl_process = subprocess.Popen(cmd)
        
        self.mission_id = mission_id
        if self.mission_origin is not None and self.camera_profile is not None:
            self.survivor_locator = LiveSurvivorGeolocator(
                mission_id, self.mission_origin, self.camera_profile, vehicle_id=self.vehicle_id,
                mission_region_validator=self.mission_region_validator,
            )
            print(f"[Surveillance] Live survivor geolocation ready for {self.vehicle_id} (SIMULATION_FLAT_GROUND)")
        else:
            self.survivor_locator = None
            print("[Surveillance] Live survivor geolocation unavailable: mission camera geometry or origin missing")

    def update_vehicle_pose(self, vehicle, execution_state=None) -> None:
        """Receive MAVLink-normalized pose estimates from the existing runner."""
        self.registration_enabled = allows_survivor_registration(execution_state)
        if self.survivor_locator is not None:
            self.survivor_locator.update_vehicle_pose(vehicle)

    def _frame_id(self) -> str:
        # Preserve the accepted single-drone frame IDs; fleet IDs cannot clash.
        return f"{self.vehicle_id}:camera:{self.frame_count}" if self.precise_helper_stop else f"camera:{self.frame_count}"

    async def stop(self):
        self.is_running = False
        self._is_inferring = False
        print(f"[Surveillance] Stopping for mission {self.mission_id}")
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=3.0)
            except Exception:
                pass
        if self.wsl_process:
            try:
                self.wsl_process.kill()
            except Exception:
                pass
            def _kill_helper():
                try:
                    if self.helper_pid_file:
                        # Fleet helpers are independently owned. Never kill the
                        # other vehicle's bridge (or an unrelated user's helper).
                        subprocess.run(
                            ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c",
                             f"if test -f {self.helper_pid_file}; then "
                             f"read -r pid < {self.helper_pid_file}; "
                             "case $pid in *[!0-9]*|'') exit 1;; esac; "
                             f"if tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -Fq {shlex.quote(str(self.port))}; then kill -TERM $pid 2>/dev/null || true; fi; "
                             f"rm -f {self.helper_pid_file}; fi"],
                            capture_output=True, text=True, check=False, timeout=5.0,
                        )
                        return
                    subprocess.run(
                        ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc",
                         "pkill -9 -f camera_stream_helper || true"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5.0,
                    )
                except Exception:
                    pass
            try:
                await asyncio.to_thread(_kill_helper)
            except Exception:
                pass
            self.wsl_process = None

    async def handle_client(self, reader, writer):
        print(f"[Surveillance] Client connected from WSL bridge")
        legacy_header = struct.Struct('<4sdIII')
        modern_header = struct.Struct('<4sdIIIII')
        try:
            while self.is_running:
                magic = await reader.readexactly(4)
                if magic == b'CAM2':
                    _, ts, width, height, stride, pixel_format, size = modern_header.unpack(magic + await reader.readexactly(modern_header.size - 4))
                elif magic == b'CAM!':
                    _, ts, width, height, size = legacy_header.unpack(magic + await reader.readexactly(legacy_header.size - 4))
                    stride, pixel_format = width, 0
                else:
                    raise ValueError(f"invalid camera magic {magic!r}")
                if size <= 0 or size > 32 * 1024 * 1024:
                    raise ValueError(f"invalid camera payload size {size}")
                frame_data = await reader.readexactly(size)
                frame_received_monotonic_ns = time.monotonic_ns()
                self.frame_count += 1
                self.last_frame_at = time.time()
                if self.frame_count % 30 == 0:
                    print(f"[Surveillance] received={self.frame_count} {width}x{height} stride={stride} format={pixel_format} bytes={size}")
                
                if stride < width or size != stride * height:
                    raise ValueError(f"invalid image layout {width}x{height}, stride={stride}, bytes={size}")
                img = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, stride))[:, :width]
                self.decoded_frame_count += 1
                # YOLO expects 3 channels
                img_rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
                sample_brightness = self.frame_count == 1 or self.frame_count % 30 == 0
                if sample_brightness:
                    # Source bytes, decoded mono, and model input are kept as
                    # distinct named stages so a future pixel conversion fault
                    # is immediately localized.  These measurements are
                    # observational only; no gain or normalization is applied.
                    self.latest_brightness = {
                        "sample_frame_id": f"camera:{self.frame_count}",
                        "source": brightness_statistics(np.frombuffer(frame_data, dtype=np.uint8)),
                        "decoded": brightness_statistics(img),
                        "model_input": brightness_statistics(img_rgb),
                    }
                
                detections = []
                inference_time = None
                model_status = "ERROR" if self.model is None else "READY"
                model_error = self.model_error
                if self.model is not None and not self._is_inferring:
                    self._is_inferring = True
                    try:
                        t0 = time.time()
                        def _infer(m, i):
                            if self.inference_lock is not None:
                                with self.inference_lock:
                                    return m(
                                        i, conf=ACTIVE_DETECTION_CONFIDENCE,
                                        imgsz=ACTIVE_INFERENCE_SIZE, verbose=False,
                                    )[0]
                            return m(
                                i, conf=ACTIVE_DETECTION_CONFIDENCE,
                                imgsz=ACTIVE_INFERENCE_SIZE, verbose=False,
                            )[0]
                        results = await asyncio.to_thread(_infer, self.model, img_rgb)
                        inference_time = time.time() - t0
                        self.inference_count += 1
                        for box in results.boxes:
                            conf = float(box.conf[0])
                            if accepts_active_detection(conf):
                                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
                                class_id = int(box.cls[0])
                                model_label = str(results.names.get(class_id, class_id))
                                detections.append({
                                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                                    "conf": conf,
                                    "class": model_label,
                                    "class_id": class_id,
                                    "display_label": (
                                        detection_display_label(self.model_path, class_id, model_label)
                                        if self.model_path is not None else model_label
                                    ),
                                })
                    except Exception as exc:
                        model_status = "ERROR"
                        model_error = f"inference failed: {exc}"
                        self.latest_error = model_error
                        print(f"[Surveillance] {model_error}")
                    finally:
                        self._is_inferring = False

                geolocation_status = "UNAVAILABLE"
                geolocation_reason = "MISSION_CAMERA_GEOMETRY_UNAVAILABLE"
                if self.survivor_locator is not None and self.registration_enabled:
                    try:
                        outcomes = self.survivor_locator.observe(
                            frame_id=self._frame_id(),
                            timestamp_monotonic_ns=frame_received_monotonic_ns,
                            detections=detections, width=width, height=height,
                        )
                        projected = iter(outcomes)
                        for detection in detections:
                            if detection["class_id"] != 0:
                                continue
                            outcome = next(projected, None)
                            if outcome is None:
                                continue
                            if outcome.observation is None:
                                detection["geolocation"] = {"status": "UNAVAILABLE", "reason": outcome.reason}
                                geolocation_reason = outcome.reason or geolocation_reason
                                continue
                            observation = outcome.observation
                            if self.observation_callback is not None:
                                try:
                                    self.observation_callback(self.vehicle_id, observation, outcome.estimate)
                                except Exception as exc:
                                    self.latest_error = f"fleet observation callback: {exc}"
                            target_id = observation.target_track_id
                            detection["geolocation"] = {
                                "status": "APPROXIMATE", "target_id": target_id,
                                "latitude": observation.target_latitude, "longitude": observation.target_longitude,
                                "east_m": observation.target_east_m, "north_m": observation.target_north_m,
                                "uncertainty_m": observation.position_uncertainty_m,
                                "quality": observation.quality.value,
                            }
                            geolocation_status, geolocation_reason = "APPROXIMATE", None
                            if self.geolocation_service is not None and target_id:
                                last = self._target_publish_at.get(target_id, 0.0)
                                # Persist every initial observation; then keep target/event traffic
                                # bounded while the per-frame camera image remains current.
                                if last == 0.0 or time.monotonic() - last >= 0.2:
                                    await self.geolocation_service.publish_observation(self.mission_id, observation)
                                    # A tracker candidate is deliberately not a
                                    # registered survivor.  It remains private
                                    # to the mission-scoped tracker until the
                                    # existing confirmation evidence is met;
                                    # otherwise brief projection jitter creates
                                    # visible duplicate survivor records.
                                    if is_persistent_survivor_estimate(outcome.estimate, self.mission_id):
                                        await self.geolocation_service.publish_target(outcome.estimate)
                                    self._target_publish_at[target_id] = time.monotonic()
                        if self.geolocation_service is not None:
                            for superseded_id, canonical_id in self.survivor_locator.pop_merged_events():
                                await self.geolocation_service.publish_target_superseded(superseded_id, canonical_id)
                                canonical_track = self.survivor_locator.tracker.tracks.get(canonical_id)
                                if canonical_track and is_persistent_survivor_estimate(canonical_track.estimate, self.mission_id):
                                    await self.geolocation_service.publish_target(canonical_track.estimate)
                    except Exception as exc:
                        geolocation_status = "UNAVAILABLE"
                        geolocation_reason = f"GEOLOCATION_ERROR: {exc}"
                        self.latest_error = geolocation_reason
                elif self.survivor_locator is not None:
                    geolocation_reason = "MISSION_NOT_SEARCHING"
                        
                # The image stays unannotated: source-pixel detections below
                # are rendered by the UI against this exact frame.
                display_img = cv2.resize(img_rgb, (640, 400))
                encoded, buffer = cv2.imencode('.jpg', display_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if not encoded:
                    raise RuntimeError("JPEG encoding failed")
                if sample_brightness and self.latest_brightness is not None:
                    jpeg_pixels = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
                    if jpeg_pixels is not None:
                        self.latest_brightness["jpeg"] = brightness_statistics(jpeg_pixels)
                b64_img = base64.b64encode(buffer).decode('utf-8')
                
                # Broadcast
                msg = RealtimeEnvelope(
                    type="camera_frame",
                    mission_id=self.mission_id,
                    vehicle_id=self.vehicle_id,
                    sequence=0,
                    payload={
                        "inference_time": inference_time,
                        "image_b64": b64_img,
                        "detections": detections,
                        "frame_id": self._frame_id(),
                        "detection_image_width": width,
                        "detection_image_height": height,
                        "image_width": 640,
                        "image_height": 400,
                        "freshness": time.time() - ts,
                        "source_ts": ts,
                        "frame_count": self.frame_count,
                        "decoded_frame_count": self.decoded_frame_count,
                        "inference_count": self.inference_count,
                        "camera_status": "LIVE",
                        "model_status": model_status,
                        "model_error": model_error,
                        "geolocation_status": geolocation_status,
                        "geolocation_reason": geolocation_reason,
                        "brightness": self.latest_brightness,
                    }
                )
                
                # Use WebSocketManager
                await self.websocket_manager.broadcast(msg)
                
        except asyncio.IncompleteReadError:
            pass
        except Exception as e:
            self.latest_error = str(e)
            print(f"[Surveillance] frame pipeline error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
