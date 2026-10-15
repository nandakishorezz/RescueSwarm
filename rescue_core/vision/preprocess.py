"""Monochrome-aware letterboxing, tiling, coordinate restoration, and NMS."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .models import Detection, FramePacket


@dataclass(slots=True, frozen=True)
class Transform:
    original_width: int
    original_height: int
    input_width: int
    input_height: int
    scale: float
    pad_x: float
    pad_y: float
    tile_x: int = 0
    tile_y: int = 0


@dataclass(slots=True)
class PreparedInput:
    tensor: np.ndarray
    transform: Transform
    original_channel_format: str
    model_input_format: str = "RGB_FLOAT32_CHW"


@dataclass(slots=True, frozen=True)
class RawDetection:
    class_id: int
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float


class VisionPreprocessor:
    def __init__(
        self, input_width: int, input_height: int, normalization: str = "zero_to_one",
        strategy: str = "full_frame", tile_width: int = 640, tile_height: int = 640,
        tile_overlap: float = 0.2,
    ):
        if input_width <= 0 or input_height <= 0:
            raise ValueError("Model input dimensions must be positive")
        if strategy not in {"full_frame", "tiled"}:
            raise ValueError("Preprocess strategy must be full_frame or tiled")
        if not 0 <= tile_overlap < 0.9:
            raise ValueError("Tile overlap must be in [0, 0.9)")
        self.input_width, self.input_height = input_width, input_height
        self.normalization = normalization
        self.strategy = strategy
        self.tile_width, self.tile_height = tile_width, tile_height
        self.tile_overlap = tile_overlap

    def prepare(self, frame: FramePacket) -> list[PreparedInput]:
        if self.strategy == "full_frame":
            return [self._letterbox(frame.image, frame.pixel_format, 0, 0)]
        prepared: list[PreparedInput] = []
        for x, y, width, height in tile_regions(
            frame.width, frame.height, self.tile_width, self.tile_height, self.tile_overlap
        ):
            prepared.append(self._letterbox(frame.image[y:y + height, x:x + width], frame.pixel_format, x, y))
        return prepared

    def restore(
        self, raw: RawDetection, prepared: PreparedInput,
        frame: FramePacket, class_name: str,
    ) -> Detection:
        transform = prepared.transform
        x1 = (raw.x1 - transform.pad_x) / transform.scale + transform.tile_x
        y1 = (raw.y1 - transform.pad_y) / transform.scale + transform.tile_y
        x2 = (raw.x2 - transform.pad_x) / transform.scale + transform.tile_x
        y2 = (raw.y2 - transform.pad_y) / transform.scale + transform.tile_y
        return Detection(
            class_id=raw.class_id,
            class_name=class_name,
            confidence=raw.confidence,
            x1=min(frame.width, max(0, x1)),
            y1=min(frame.height, max(0, y1)),
            x2=min(frame.width, max(0, x2)),
            y2=min(frame.height, max(0, y2)),
            frame_width=frame.width,
            frame_height=frame.height,
        )

    def _letterbox(self, source: np.ndarray, pixel_format: str, tile_x: int, tile_y: int) -> PreparedInput:
        rgb = to_rgb(source, pixel_format)
        source_height, source_width = rgb.shape[:2]
        scale = min(self.input_width / source_width, self.input_height / source_height)
        resized_width = max(1, round(source_width * scale))
        resized_height = max(1, round(source_height * scale))
        resized = np.asarray(
            Image.fromarray(rgb).resize((resized_width, resized_height), Image.Resampling.BILINEAR),
            dtype=np.uint8,
        )
        left = (self.input_width - resized_width) // 2
        top = (self.input_height - resized_height) // 2
        canvas = np.full((self.input_height, self.input_width, 3), 114, dtype=np.uint8)
        canvas[top:top + resized_height, left:left + resized_width] = resized
        tensor = canvas.astype(np.float32)
        if self.normalization == "zero_to_one":
            tensor /= 255.0
        elif self.normalization != "none":
            raise ValueError(f"Unsupported normalization: {self.normalization}")
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None, ...])
        return PreparedInput(
            tensor=tensor,
            transform=Transform(
                source_width, source_height, self.input_width, self.input_height,
                scale, float(left), float(top), tile_x, tile_y,
            ),
            original_channel_format=pixel_format,
        )


def to_rgb(image: np.ndarray, pixel_format: str) -> np.ndarray:
    if image.ndim == 2:
        return np.repeat(image[:, :, None], 3, axis=2)
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError(f"Unsupported image shape: {image.shape}")
    channels = image[:, :, :3]
    if pixel_format.upper().startswith("BGR"):
        return channels[:, :, ::-1].copy()
    return channels.copy()


def tile_regions(width: int, height: int, tile_width: int, tile_height: int, overlap: float) -> list[tuple[int, int, int, int]]:
    if min(width, height, tile_width, tile_height) <= 0:
        raise ValueError("Frame and tile dimensions must be positive")
    actual_width, actual_height = min(tile_width, width), min(tile_height, height)
    step_x = max(1, round(actual_width * (1 - overlap)))
    step_y = max(1, round(actual_height * (1 - overlap)))
    xs = _starts(width, actual_width, step_x)
    ys = _starts(height, actual_height, step_y)
    return [(x, y, actual_width, actual_height) for y in ys for x in xs]


def _starts(full: int, tile: int, step: int) -> list[int]:
    starts = list(range(0, max(1, full - tile + 1), step))
    final = full - tile
    if starts[-1] != final:
        starts.append(final)
    return starts


def intersection_over_union(left: Detection, right: Detection) -> float:
    x1, y1 = max(left.x1, right.x1), max(left.y1, right.y1)
    x2, y2 = min(left.x2, right.x2), min(left.y2, right.y2)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = left.area_px + right.area_px - intersection
    return intersection / union if union > 0 else 0


def non_maximum_suppression(detections: list[Detection], iou_threshold: float) -> list[Detection]:
    kept: list[Detection] = []
    for candidate in sorted(detections, key=lambda item: (-item.confidence, item.class_id, item.x1, item.y1)):
        if all(
            candidate.class_id != accepted.class_id
            or intersection_over_union(candidate, accepted) <= iou_threshold
            for accepted in kept
        ):
            kept.append(candidate)
    return kept
