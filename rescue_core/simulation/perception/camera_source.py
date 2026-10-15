"""Stage-4 CameraSource backed by live Gazebo-rendered pixels."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from rescue_core.vision.camera import CameraSource, CameraSourceError
from rescue_core.vision.models import CameraMetadata, FramePacket
from rescue_core.simulation.sensor_realism import CameraRealism


@dataclass(slots=True)
class GazeboFrameTransportMetrics:
    frames_generated: int = 0
    frames_received: int = 0
    frames_dropped_bridge: int = 0
    duplicate_frames: int = 0
    source_sequence_gaps: int = 0
    checksum_failures: int = 0
    first_sim_time_ns: int | None = None
    last_sim_time_ns: int | None = None
    first_wall_receive_ns: int | None = None
    last_wall_receive_ns: int | None = None
    max_frame_age_ms: float | None = None
    out_of_order_frames: int = 0
    repeated_timestamps: int = 0
    simulation_time_resets: int = 0

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        sim_span = ((self.last_sim_time_ns or 0) - (self.first_sim_time_ns or 0)) / 1e9
        wall_span = ((self.last_wall_receive_ns or 0) - (self.first_wall_receive_ns or 0)) / 1e9
        result["simulation_time_source_fps"] = (
            (self.frames_received - 1) / sim_span if self.frames_received > 1 and sim_span > 0 else 0.0
        )
        result["wall_time_receive_fps"] = (
            (self.frames_received - 1) / wall_span if self.frames_received > 1 and wall_span > 0 else 0.0
        )
        return result


@dataclass(slots=True)
class CameraSensorRealismMetrics:
    frames_delayed: int = 0
    frames_released: int = 0
    frames_dropped: int = 0
    max_pending_delayed_frames: int = 0
    out_of_order_releases: int = 0
    configured_latency_ms: float = 0.0
    configured_jitter_ms: float = 0.0
    observed_delivery_delay_p50_ms: float | None = None
    observed_delivery_delay_p95_ms: float | None = None
    image_mean_absolute_change_p50_dn: float | None = None


class SensorRealismCameraSource(CameraSource):
    """Bounded sensor/transport adapter for already-rendered MONO8 frames.

    Capture identity and simulation timestamp are immutable.  A background
    producer applies a seeded monochrome measurement transform and schedules
    ordered delivery on host monotonic time.  Host time controls transport
    release only; it is never a pose-selection key.
    """

    def __init__(
        self,
        source: CameraSource,
        profile: CameraRealism,
        *,
        sensor_seed: int,
    ) -> None:
        super().__init__(source.source_id)
        self.source = source
        self.profile = profile
        self.sensor_seed = int(sensor_seed)
        self.metrics = CameraSensorRealismMetrics(
            configured_latency_ms=profile.latency_ms,
            configured_jitter_ms=profile.jitter_ms,
        )
        self._pending: deque[tuple[int, FramePacket]] = deque()
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._reader: threading.Thread | None = None
        self._error: BaseException | None = None
        self._ended = False
        self._last_release_deadline_ns = 0
        self._last_released_sequence = -1
        self._delivery_delays_ms: deque[float] = deque(maxlen=4096)
        self._image_delta_dn: deque[float] = deque(maxlen=4096)

    def start(self) -> None:
        if self._reader and self._reader.is_alive():
            return
        self.source.start()
        self._stop_event.clear()
        self._ended = False
        self._error = None
        self._reader = threading.Thread(target=self._produce, name="camera-sensor-realism", daemon=True)
        self._reader.start()

    def _produce(self) -> None:
        try:
            while not self._stop_event.is_set():
                packet = self.source.read()
                if packet is None:
                    break
                received_ns = time.monotonic_ns()
                rng = np.random.default_rng(np.random.SeedSequence([self.sensor_seed, packet.sequence]))
                jitter_ms = (
                    float(rng.uniform(-self.profile.jitter_ms, self.profile.jitter_ms))
                    if self.profile.jitter_ms else 0.0
                )
                scheduled_ns = received_ns + int(max(0.0, self.profile.latency_ms + jitter_ms) * 1e6)
                # Nominal Phase 3 explicitly preserves order.
                scheduled_ns = max(scheduled_ns, self._last_release_deadline_ns + 1)
                self._last_release_deadline_ns = scheduled_ns
                original = packet.image
                image = self._measurement_image(original, rng)
                delta = float(np.mean(np.abs(image.astype(np.int16) - original.astype(np.int16))))
                extra = dict(packet.camera_metadata.extra)
                extra.update({
                    "sensor_realism_applied": True,
                    "camera_transport_latency_ms": self.profile.latency_ms,
                    "camera_transport_jitter_sample_ms": jitter_ms,
                    "camera_received_monotonic_ns": received_ns,
                    "camera_scheduled_delivery_monotonic_ns": scheduled_ns,
                    "capture_timestamp_preserved": True,
                    "host_time_authoritative_for_pose": False,
                    "image_noise_stddev_dn": self.profile.gaussian_noise_stddev_dn,
                    "exposure_preset": self.profile.exposure_preset,
                    "rolling_shutter_model": self.profile.rolling_shutter_model,
                    "motion_blur_model": self.profile.motion_blur_model,
                })
                packet.image = image
                packet.camera_metadata = CameraMetadata(
                    exposure_time_us=self.profile.exposure_time_us,
                    analogue_gain=packet.camera_metadata.analogue_gain,
                    digital_gain=packet.camera_metadata.digital_gain,
                    sensor_timestamp_ns=packet.camera_metadata.sensor_timestamp_ns,
                    frame_duration_us=packet.camera_metadata.frame_duration_us,
                    extra=extra,
                )
                with self._condition:
                    if len(self._pending) >= self.profile.max_pending_frames:
                        self._pending.popleft()
                        self.metrics.frames_dropped += 1
                    self._pending.append((scheduled_ns, packet))
                    self.metrics.frames_delayed += 1
                    self.metrics.max_pending_delayed_frames = max(
                        self.metrics.max_pending_delayed_frames, len(self._pending),
                    )
                    self._image_delta_dn.append(delta)
                    self._condition.notify_all()
        except BaseException as exc:
            if not self._stop_event.is_set():
                self._error = exc
        finally:
            with self._condition:
                self._ended = True
                self._condition.notify_all()

    def _measurement_image(self, image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        measured = image.astype(np.float32)
        measured = (measured - 127.5) * self.profile.contrast_gain + 127.5
        measured *= self.profile.brightness_gain
        if self.profile.gaussian_noise_stddev_dn:
            measured += rng.normal(0.0, self.profile.gaussian_noise_stddev_dn, image.shape)
        return np.clip(np.rint(measured), 0, 255).astype(np.uint8)

    def read(self) -> FramePacket | None:
        while True:
            with self._condition:
                if self._pending:
                    deadline_ns, packet = self._pending[0]
                    remaining_s = (deadline_ns - time.monotonic_ns()) / 1e9
                    if remaining_s <= 0:
                        self._pending.popleft()
                        if packet.sequence <= self._last_released_sequence:
                            self.metrics.out_of_order_releases += 1
                            raise CameraSourceError("camera realism adapter reordered a frame")
                        self._last_released_sequence = packet.sequence
                        delivered_ns = time.monotonic_ns()
                        packet.camera_metadata.extra["camera_delivery_monotonic_ns"] = delivered_ns
                        delay_ms = (delivered_ns - int(packet.camera_metadata.extra["camera_received_monotonic_ns"])) / 1e6
                        self._delivery_delays_ms.append(delay_ms)
                        self.metrics.frames_released += 1
                        self._refresh_percentiles()
                        return packet
                    self._condition.wait(timeout=min(remaining_s, 0.1))
                    continue
                if self._error is not None:
                    raise CameraSourceError(f"camera realism producer failed: {self._error}") from self._error
                if self._ended:
                    return None
                self._condition.wait(timeout=0.1)

    def _refresh_percentiles(self) -> None:
        if self._delivery_delays_ms:
            values = np.fromiter(self._delivery_delays_ms, dtype=np.float64)
            self.metrics.observed_delivery_delay_p50_ms = float(np.percentile(values, 50))
            self.metrics.observed_delivery_delay_p95_ms = float(np.percentile(values, 95))
        if self._image_delta_dn:
            values = np.fromiter(self._image_delta_dn, dtype=np.float64)
            self.metrics.image_mean_absolute_change_p50_dn = float(np.percentile(values, 50))

    def stop(self) -> None:
        self._stop_event.set()
        self.source.stop()
        with self._condition:
            self._condition.notify_all()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=5)
        self._reader = None
        with self._condition:
            self._pending.clear()


class GazeboCameraSource(CameraSource):
    """One live, local-only producer for a Gazebo image sensor.

    Gazebo sensor time is preserved as sensor metadata.  The FramePacket host
    monotonic timestamp is the local bridge receipt time: simulation time can
    advance slower or faster than wall time, so projecting it onto the host
    monotonic clock would incorrectly make fresh frames look stale whenever
    real-time factor differs from one.
    """

    L_INT8 = 1
    RGB_INT8 = 3
    RGBA_INT8 = 4
    BGR_INT8 = 8

    def __init__(
        self,
        topic: str,
        *,
        source_id: str = "gazebo_camera",
        camera_profile_id: str = "ov9281_reference_simulation",
        expected_width: int = 1280,
        expected_height: int = 800,
        bridge_capacity: int = 3,
        wsl_distribution: str = "Ubuntu",
        bridge_port: int | None = None,
        bridge_script: str | Path | None = None,
        convert_to_mono: bool = True,
    ) -> None:
        super().__init__(source_id)
        if bridge_capacity not in {2, 3, 4}:
            raise ValueError("Gazebo bridge capacity must be between two and four frames")
        self.topic = topic
        self.camera_profile_id = camera_profile_id
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.bridge_capacity = bridge_capacity
        self.wsl_distribution = wsl_distribution
        self.bridge_port = bridge_port
        self.bridge_script = Path(bridge_script or Path(__file__).parents[1] / "gazebo_bridge" / "image_bridge.py").resolve()
        self.convert_to_mono = convert_to_mono
        self.metrics = GazeboFrameTransportMetrics()
        self._process: subprocess.Popen[str] | None = None
        self._socket: socket.socket | None = None
        self._linux_pid: int | None = None
        self._started = False
        self._last_source_sequence: int | None = None
        self._last_capture_monotonic_ns: int | None = None
        # Startup failure cleanup calls stop while holding the lifecycle lock.
        self._lock = threading.RLock()
        # Optional: called with (sim_time_ns, host_receive_ns) after each frame.
        # Used by DirectSimClockWitness to observe Gazebo timestamps.
        # Must contain ONLY timing information — no image data, no world state.
        self.frame_witness_callback: "Callable[[int, int], None] | None" = None


    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            port = self.bridge_port or _free_loopback_port()
            self.bridge_port = port
            pid_file = self.bridge_script.parent / f".image_bridge_{port}.pid"
            try:
                pid_file.unlink(missing_ok=True)
            except OSError:
                pass
            command = [
                "wsl.exe", "-d", self.wsl_distribution, "--", "python3",
                _wsl_path(self.bridge_script), "--topic", self.topic,
                "--port", str(port), "--capacity", str(self.bridge_capacity),
                "--pid-file", _wsl_path(pid_file),
            ]
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if pid_file.exists():
                    try:
                        self._linux_pid = int(pid_file.read_text(encoding="ascii").strip())
                    except (OSError, ValueError):
                        pass
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read() if self._process.stderr else ""
                    self.stop()
                    raise CameraSourceError(f"Gazebo image bridge exited during startup: {stderr.strip()}")
                try:
                    self._socket = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                    self._socket.settimeout(300.0)
                    self._last_source_sequence = None
                    self._started = True
                    return
                except OSError:
                    time.sleep(0.1)
            self.stop()
            raise CameraSourceError(f"Timed out connecting to Gazebo image bridge on loopback port {port}")

    def read(self) -> FramePacket | None:
        if not self._started or self._socket is None:
            raise CameraSourceError("Gazebo camera source is not started")
        connection = self._socket
        header_length = struct.unpack("!I", _recv_exact(connection, 4))[0]
        if not 0 < header_length <= 65536:
            raise CameraSourceError(f"Invalid image bridge header length: {header_length}")
        try:
            header = json.loads(_recv_exact(connection, header_length).decode("utf-8"))
            width, height = int(header["width"]), int(header["height"])
            pixel_format_type = int(header["pixel_format_type"])
            step, data_length = int(header["step"]), int(header["data_length"])
            channels = {self.L_INT8: 1, self.RGB_INT8: 3, self.RGBA_INT8: 4, self.BGR_INT8: 3}.get(pixel_format_type)
            if (width, height) != (self.expected_width, self.expected_height):
                raise ValueError("camera resolution does not match required profile")
            if channels is None or not width * channels <= step <= width * channels + 4096:
                raise ValueError("unsupported pixel format or invalid row stride")
            if data_length != step * height:
                raise ValueError("payload length does not match image stride and height")
        except (KeyError, TypeError, ValueError) as exc:
            raise CameraSourceError(f"Invalid Gazebo image header: {exc}") from exc
        pixels = _recv_exact(connection, data_length)
        import hashlib
        if hashlib.sha256(pixels).hexdigest() != header["checksum_sha256"]:
            self.metrics.checksum_failures += 1
            raise CameraSourceError("Gazebo frame checksum failed")

        if self.convert_to_mono:
            image = _mono8(pixels, width, height, int(header["step"]), pixel_format_type)
            packet_pixel_format = "MONO8"
        else:
            image, packet_pixel_format = _native_pixels(
                pixels, width, height, int(header["step"]), pixel_format_type,
            )
        sim_time_ns = int(header["sim_capture_time_ns"])
        bridge_receive_ns = int(header["bridge_receive_time_ns"])
        host_receive_ns = time.monotonic_ns()
        wall_receive_ns = time.time_ns()
        # Notify clock witness immediately with timing only — no image data.
        if self.frame_witness_callback is not None:
            try:
                self.frame_witness_callback(sim_time_ns, host_receive_ns)
            except Exception:
                pass  # Witness errors must never block the camera pipeline.
        capture_monotonic_ns = host_receive_ns
        if self._last_capture_monotonic_ns is not None:
            capture_monotonic_ns = max(capture_monotonic_ns, self._last_capture_monotonic_ns + 1)
        self._last_capture_monotonic_ns = capture_monotonic_ns
        if self.metrics.last_sim_time_ns is not None and sim_time_ns < self.metrics.last_sim_time_ns:
            self.metrics.simulation_time_resets += 1
            raise CameraSourceError("Gazebo capture clock moved backwards; start a new run epoch")
        source_sequence = int(header["source_sequence"])
        sequence_gap = 0
        if self._last_source_sequence is not None:
            if source_sequence <= self._last_source_sequence:
                self.metrics.out_of_order_frames += 1
                raise CameraSourceError("Duplicate or out-of-order Gazebo frame identity")
            sequence_gap = source_sequence - self._last_source_sequence - 1
            self.metrics.source_sequence_gaps += sequence_gap
        if sim_time_ns == self.metrics.last_sim_time_ns:
            self.metrics.repeated_timestamps += 1
            raise CameraSourceError("Duplicate Gazebo capture timestamp")
        self._last_source_sequence = source_sequence

        self.metrics.frames_generated = int(header["frames_generated"])
        self.metrics.frames_received += 1
        self.metrics.frames_dropped_bridge = int(header["frames_dropped_bridge"])
        self.metrics.duplicate_frames = int(header["duplicate_frames"])
        if self.metrics.first_sim_time_ns is None:
            self.metrics.first_sim_time_ns = sim_time_ns
        self.metrics.last_sim_time_ns = sim_time_ns
        self.metrics.first_wall_receive_ns = self.metrics.first_wall_receive_ns or wall_receive_ns
        self.metrics.last_wall_receive_ns = wall_receive_ns

        metadata = CameraMetadata(
            sensor_timestamp_ns=sim_time_ns,
            extra={
                "execution_source": "SITL_GAZEBO",
                "camera_source": "GAZEBO_RENDERED",
                "camera_profile_id": self.camera_profile_id,
                "sim_capture_time_ns": sim_time_ns,
                "capture_timestamp_source": header.get("capture_timestamp_source"),
                "capture_stamp_sec": header.get("capture_stamp_sec"),
                "capture_stamp_nsec": header.get("capture_stamp_nsec"),
                "bridge_receive_time_ns": bridge_receive_ns,
                "source_receive_time_ns": host_receive_ns,
                "capture_clock_mapping": "UNVERIFIED_RECEIPT_TIME_PROXY",
                "capture_pose_alignment_verified": False,
                "source_sequence": source_sequence,
                "checksum_sha256": header["checksum_sha256"],
                "raw_min": header.get("raw_min"),
                "raw_max": header.get("raw_max"),
                "raw_mean": header.get("raw_mean"),
                "raw_std": header.get("raw_std"),
                "frames_generated": self.metrics.frames_generated,
                "frames_dropped_bridge": self.metrics.frames_dropped_bridge,
                "duplicate_frames": self.metrics.duplicate_frames,
            },
        )
        packet = self._packet(
            image, packet_pixel_format, metadata,
            capture_monotonic_ns=capture_monotonic_ns,
            capture_wall_ns=wall_receive_ns,
            capture_sim_ns=sim_time_ns,
        )
        packet.dropped_since_previous = sequence_gap
        return packet

    def stop(self) -> None:
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._socket.close()
                self._socket = None
            if self._linux_pid is not None:
                subprocess.run(
                    ["wsl.exe", "-d", self.wsl_distribution, "--", "kill", str(self._linux_pid)],
                    capture_output=True, text=True, timeout=5,
                )
            if self._process is not None:
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                self._process = None
            self._linux_pid = None
            self._started = False


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    if length < 0:
        raise CameraSourceError("Negative image bridge payload length")
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise CameraSourceError("Gazebo image bridge disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _mono8(pixels: bytes, width: int, height: int, step: int, pixel_format: int) -> np.ndarray:
    raw = np.frombuffer(pixels, dtype=np.uint8)
    if pixel_format == GazeboCameraSource.L_INT8:
        rows = raw.reshape(height, step)
        return rows[:, :width].copy()
    channels = 4 if pixel_format == GazeboCameraSource.RGBA_INT8 else 3
    if pixel_format not in {GazeboCameraSource.RGB_INT8, GazeboCameraSource.RGBA_INT8, GazeboCameraSource.BGR_INT8}:
        raise CameraSourceError(f"Unsupported Gazebo pixel format enum: {pixel_format}")
    rows = raw.reshape(height, step)[:, : width * channels].reshape(height, width, channels)
    if pixel_format == GazeboCameraSource.BGR_INT8:
        blue, green, red = rows[..., 0], rows[..., 1], rows[..., 2]
    else:
        red, green, blue = rows[..., 0], rows[..., 1], rows[..., 2]
    # Deterministic BT.601 full-range integer luminance approximation.
    return ((77 * red.astype(np.uint16) + 150 * green.astype(np.uint16) + 29 * blue.astype(np.uint16)) >> 8).astype(np.uint8)


def _native_pixels(
    pixels: bytes, width: int, height: int, step: int, pixel_format: int,
) -> tuple[np.ndarray, str]:
    raw = np.frombuffer(pixels, dtype=np.uint8)
    if pixel_format == GazeboCameraSource.L_INT8:
        return raw.reshape(height, step)[:, :width].copy(), "L_INT8"
    channels = 4 if pixel_format == GazeboCameraSource.RGBA_INT8 else 3
    if pixel_format not in {
        GazeboCameraSource.RGB_INT8,
        GazeboCameraSource.RGBA_INT8,
        GazeboCameraSource.BGR_INT8,
    }:
        raise CameraSourceError(f"Unsupported Gazebo native pixel format enum: {pixel_format}")
    image = raw.reshape(height, step)[:, : width * channels].reshape(height, width, channels).copy()
    names = {
        GazeboCameraSource.RGB_INT8: "RGB_INT8",
        GazeboCameraSource.RGBA_INT8: "RGBA_INT8",
        GazeboCameraSource.BGR_INT8: "BGR_INT8",
    }
    return image, names[pixel_format]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wsl_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    posix = resolved.as_posix()
    for prefix in ("//wsl.localhost/Ubuntu", "//wsl$/Ubuntu"):
        if posix.startswith(prefix):
            return posix[len(prefix):]
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{posix.split(':', 1)[1].lstrip('/')}"
