"""Frame and detection contracts with original-pixel coordinate integrity."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass(slots=True)
class CameraMetadata:
    exposure_time_us: float | None = None
    analogue_gain: float | None = None
    digital_gain: float | None = None
    sensor_timestamp_ns: int | None = None
    frame_duration_us: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exposure_time_us": self.exposure_time_us,
            "analogue_gain": self.analogue_gain,
            "digital_gain": self.digital_gain,
            "sensor_timestamp_ns": self.sensor_timestamp_ns,
            "frame_duration_us": self.frame_duration_us,
            "extra": self.extra,
        }


@dataclass(slots=True)
class FramePacket:
    frame_id: str
    source_id: str
    capture_timestamp_monotonic_ns: int
    capture_timestamp_wall_ns: int
    sequence: int
    width: int
    height: int
    pixel_format: str
    image: NDArray[np.uint8]
    camera_metadata: CameraMetadata = field(default_factory=CameraMetadata)
    dropped_since_previous: int = 0
    capture_timestamp_sim_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.frame_id or not self.source_id:
            raise ValueError("Frame IDs and source IDs must be non-empty")
        if self.capture_timestamp_monotonic_ns <= 0 or self.capture_timestamp_wall_ns <= 0:
            raise ValueError("Frame timestamps must be positive nanosecond values")
        if self.sequence < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("Frame sequence and dimensions are invalid")
        if self.image.shape[:2] != (self.height, self.width):
            raise ValueError("Frame dimensions do not match the image array")
        if self.image.dtype != np.uint8:
            raise ValueError("Stage 4 frame images must use uint8 pixels")
        if self.capture_timestamp_sim_ns is not None and self.capture_timestamp_sim_ns < 0:
            raise ValueError("Simulation capture timestamp must be nonnegative")

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "source_id": self.source_id,
            "capture_timestamp_monotonic_ns": self.capture_timestamp_monotonic_ns,
            "capture_timestamp_wall_ns": self.capture_timestamp_wall_ns,
            "sequence": self.sequence,
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "camera_metadata": self.camera_metadata.as_dict(),
            "dropped_since_previous": self.dropped_since_previous,
            "capture_timestamp_sim_ns": self.capture_timestamp_sim_ns,
        }


@dataclass(slots=True, frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    frame_width: int
    frame_height: int

    def __post_init__(self) -> None:
        values = (self.confidence, self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Detection contains a non-finite value")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Detection confidence must be between zero and one")
        if not (0 <= self.x1 <= self.x2 <= self.frame_width and 0 <= self.y1 <= self.y2 <= self.frame_height):
            raise ValueError("Detection box must be in original captured-image pixels")

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def bottom_center_x(self) -> float:
        return self.center_x

    @property
    def bottom_center_y(self) -> float:
        return self.y2

    @property
    def width_px(self) -> float:
        return self.x2 - self.x1

    @property
    def height_px(self) -> float:
        return self.y2 - self.y1

    @property
    def area_px(self) -> float:
        return self.width_px * self.height_px

    def as_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox_pixels": [self.x1, self.y1, self.x2, self.y2],
            "frame_size_pixels": [self.frame_width, self.frame_height],
            "bbox_normalized": [
                self.x1 / self.frame_width, self.y1 / self.frame_height,
                self.x2 / self.frame_width, self.y2 / self.frame_height,
            ],
            "bbox_center_pixels": [self.center_x, self.center_y],
            "bbox_bottom_center_pixels": [self.bottom_center_x, self.bottom_center_y],
            "bbox_width_px": self.width_px,
            "bbox_height_px": self.height_px,
            "bbox_area_px": self.area_px,
            "bbox_area_percent": self.area_px / (self.frame_width * self.frame_height) * 100,
        }


@dataclass(slots=True)
class DetectionResult:
    frame_id: str
    source_id: str
    capture_timestamp_monotonic_ns: int
    capture_timestamp_wall_ns: int
    inference_started_ns: int
    inference_finished_ns: int
    model_id: str
    backend: str
    original_channel_format: str
    model_input_format: str
    detections: list[Detection]
    preprocess_ms: float
    inference_ms: float
    postprocess_ms: float
    end_to_end_ms: float
    capture_timestamp_sim_ns: int | None = None

    def __post_init__(self) -> None:
        if self.inference_finished_ns < self.inference_started_ns:
            raise ValueError("Inference timestamps are reversed")
        if self.inference_started_ns < self.capture_timestamp_monotonic_ns:
            raise ValueError("Inference cannot start before frame capture")

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "source_id": self.source_id,
            "capture_timestamp_monotonic_ns": self.capture_timestamp_monotonic_ns,
            "capture_timestamp_wall_ns": self.capture_timestamp_wall_ns,
            "inference_started_ns": self.inference_started_ns,
            "inference_finished_ns": self.inference_finished_ns,
            "model_id": self.model_id,
            "backend": self.backend,
            "original_channel_format": self.original_channel_format,
            "model_input_format": self.model_input_format,
            "detections": [detection.as_dict() for detection in self.detections],
            "preprocess_ms": self.preprocess_ms,
            "inference_ms": self.inference_ms,
            "postprocess_ms": self.postprocess_ms,
            "end_to_end_ms": self.end_to_end_ms,
            "capture_timestamp_sim_ns": self.capture_timestamp_sim_ns,
        }


def detection_result_from_dict(row: dict[str, Any]) -> DetectionResult:
    """Restore a normalized result from local NDJSON/event evidence."""
    detections: list[Detection] = []
    for item in row.get("detections", []):
        x1, y1, x2, y2 = item["bbox_pixels"]
        width, height = item["frame_size_pixels"]
        detections.append(Detection(
            class_id=int(item["class_id"]), class_name=str(item["class_name"]),
            confidence=float(item["confidence"]), x1=float(x1), y1=float(y1),
            x2=float(x2), y2=float(y2), frame_width=int(width), frame_height=int(height),
        ))
    return DetectionResult(
        frame_id=str(row["frame_id"]), source_id=str(row["source_id"]),
        capture_timestamp_monotonic_ns=int(row["capture_timestamp_monotonic_ns"]),
        capture_timestamp_wall_ns=int(row["capture_timestamp_wall_ns"]),
        inference_started_ns=int(row["inference_started_ns"]),
        inference_finished_ns=int(row["inference_finished_ns"]),
        model_id=str(row["model_id"]), backend=str(row["backend"]),
        original_channel_format=str(row["original_channel_format"]),
        model_input_format=str(row["model_input_format"]), detections=detections,
        preprocess_ms=float(row["preprocess_ms"]), inference_ms=float(row["inference_ms"]),
        postprocess_ms=float(row["postprocess_ms"]), end_to_end_ms=float(row["end_to_end_ms"]),
        capture_timestamp_sim_ns=(int(row["capture_timestamp_sim_ns"]) if row.get("capture_timestamp_sim_ns") is not None else None),
    )
