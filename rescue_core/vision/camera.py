"""Single-open camera/replay sources isolated from detector logic."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .models import CameraMetadata, FramePacket


class CameraSourceError(RuntimeError):
    pass


class CameraSource(ABC):
    """One authoritative producer. Implementations must not run detector logic."""

    def __init__(self, source_id: str):
        self.source_id = source_id
        self.sequence = 0

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def read(self) -> FramePacket | None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def _packet(
        self, image: np.ndarray, pixel_format: str,
        metadata: CameraMetadata | None = None,
        capture_monotonic_ns: int | None = None,
        capture_wall_ns: int | None = None,
        capture_sim_ns: int | None = None,
    ) -> FramePacket:
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        height, width = image.shape[:2]
        sequence = self.sequence
        self.sequence += 1
        return FramePacket(
            frame_id=f"{self.source_id}:{sequence:012d}",
            source_id=self.source_id,
            capture_timestamp_monotonic_ns=capture_monotonic_ns or time.monotonic_ns(),
            capture_timestamp_wall_ns=capture_wall_ns or time.time_ns(),
            sequence=sequence,
            width=width,
            height=height,
            pixel_format=pixel_format,
            image=image,
            camera_metadata=metadata or CameraMetadata(),
            capture_timestamp_sim_ns=capture_sim_ns,
        )


class SyntheticCameraSource(CameraSource):
    def __init__(self, source_id: str = "synthetic", width: int = 640, height: int = 480, fps: float = 30):
        super().__init__(source_id)
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("Synthetic camera dimensions and FPS must be positive")
        self.width, self.height, self.fps = width, height, fps
        self.pixel_format = "Y8"
        self.running = False
        self.next_frame_ns = 0

    def start(self) -> None:
        self.running = True
        self.next_frame_ns = time.monotonic_ns()

    def read(self) -> FramePacket | None:
        if not self.running:
            raise CameraSourceError("Synthetic camera is not started")
        now = time.monotonic_ns()
        if now < self.next_frame_ns:
            time.sleep((self.next_frame_ns - now) / 1e9)
        capture = time.monotonic_ns()
        self.next_frame_ns = capture + int(1e9 / self.fps)
        x = np.arange(self.width, dtype=np.uint16)[None, :]
        y = np.arange(self.height, dtype=np.uint16)[:, None]
        image = ((x + y + self.sequence * 3) % 256).astype(np.uint8)
        return self._packet(image, "Y8", capture_monotonic_ns=capture)

    def stop(self) -> None:
        self.running = False


class ImageDirectoryCameraSource(CameraSource):
    SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

    def __init__(self, directory: str | Path, source_id: str = "image_replay", loop: bool = False):
        super().__init__(source_id)
        self.directory = Path(directory)
        self.loop = loop
        self.pixel_format = "SOURCE_DEPENDENT"
        self.paths: list[Path] = []
        self.index = 0

    def start(self) -> None:
        if not self.directory.is_dir():
            raise CameraSourceError(f"Image directory does not exist: {self.directory}")
        self.paths = sorted(
            (path for path in self.directory.iterdir() if path.is_file() and path.suffix.lower() in self.SUPPORTED),
            key=lambda path: path.name.casefold(),
        )
        if not self.paths:
            raise CameraSourceError(f"No supported images in: {self.directory}")
        self.index = 0

    def read(self) -> FramePacket | None:
        if not self.paths:
            raise CameraSourceError("Image replay source is not started")
        if self.index >= len(self.paths):
            if not self.loop:
                return None
            self.index = 0
        path = self.paths[self.index]
        self.index += 1
        with Image.open(path) as opened:
            if opened.mode in {"1", "L", "I", "I;16", "F"}:
                image = np.asarray(opened.convert("L"), dtype=np.uint8)
                pixel_format = "Y8"
            else:
                image = np.asarray(opened.convert("RGB"), dtype=np.uint8)
                pixel_format = "RGB888"
        return self._packet(image, pixel_format, CameraMetadata(extra={"replay_path": path.name}))

    def stop(self) -> None:
        self.paths = []


class VideoFileCameraSource(CameraSource):
    def __init__(self, path: str | Path, source_id: str = "video_replay", loop: bool = False):
        super().__init__(source_id)
        self.path = Path(path)
        self.loop = loop
        self.pixel_format = "BGR888"
        self.capture: Any = None

    def start(self) -> None:
        if not self.path.is_file():
            raise CameraSourceError(f"Video file does not exist: {self.path}")
        try:
            import cv2
        except ImportError as exc:
            raise CameraSourceError("Video replay requires the optional OpenCV package") from exc
        self.capture = cv2.VideoCapture(str(self.path))
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = None
            raise CameraSourceError(f"Could not open video: {self.path}")

    def read(self) -> FramePacket | None:
        if self.capture is None:
            raise CameraSourceError("Video replay source is not started")
        ok, image = self.capture.read()
        if not ok and self.loop:
            import cv2
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, image = self.capture.read()
        if not ok:
            return None
        return self._packet(image, "BGR888")

    def stop(self) -> None:
        if self.capture is not None:
            self.capture.release()
        self.capture = None


class Picamera2CameraSource(CameraSource):
    """Raspberry Pi libcamera source. Picamera2 is imported only on the Pi."""

    def __init__(
        self, source_id: str, width: int, height: int, fps: float,
        pixel_format: str = "Y8", camera_index: int = 0,
    ):
        super().__init__(source_id)
        self.width, self.height, self.fps = width, height, fps
        self.pixel_format = pixel_format
        self.camera_index = camera_index
        self.camera: Any = None

    def start(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise CameraSourceError(
                "Picamera2 is unavailable. On Raspberry Pi OS install the system package and use a venv with system site packages."
            ) from exc
        self.camera = Picamera2(self.camera_index)
        libcamera_format = "YUV420" if self.pixel_format.upper() in {"Y8", "MONO8", "GRAY8"} else self.pixel_format
        configuration = self.camera.create_video_configuration(
            main={"size": (self.width, self.height), "format": libcamera_format},
            controls={"FrameRate": self.fps},
            buffer_count=4,
            queue=False,
        )
        self.camera.configure(configuration)
        self.camera.start()

    def read(self) -> FramePacket | None:
        if self.camera is None:
            raise CameraSourceError("Picamera2 camera is not started")
        request = self.camera.capture_request()
        capture_monotonic_ns = time.monotonic_ns()
        capture_wall_ns = time.time_ns()
        try:
            metadata = request.get_metadata() or {}
            image = request.make_array("main")
            request_sequence = getattr(getattr(request, "request", None), "sequence", None)
        finally:
            request.release()
        if self.pixel_format.upper() in {"Y8", "MONO8", "GRAY8"} and image.ndim == 2:
            image = image[: self.height, : self.width]
        camera_metadata = CameraMetadata(
            exposure_time_us=_number(metadata.get("ExposureTime")),
            analogue_gain=_number(metadata.get("AnalogueGain")),
            digital_gain=_number(metadata.get("DigitalGain")),
            sensor_timestamp_ns=_integer(metadata.get("SensorTimestamp")),
            frame_duration_us=_number(metadata.get("FrameDuration")),
            extra={key: value for key, value in metadata.items() if key not in {
                "ExposureTime", "AnalogueGain", "DigitalGain", "SensorTimestamp", "FrameDuration"
            }} | ({"libcamera_sequence": int(request_sequence)} if request_sequence is not None else {}),
        )
        return self._packet(
            np.asarray(image, dtype=np.uint8), self.pixel_format, camera_metadata,
            capture_monotonic_ns, capture_wall_ns,
        )

    def stop(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera.close()
        self.camera = None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
