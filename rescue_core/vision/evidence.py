"""Bounded sampled frame and person-detection evidence recording."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .models import DetectionResult, FramePacket
from .preprocess import to_rgb


class EvidenceRecorder:
    def __init__(
        self, directory: str | Path, enabled: bool = True,
        confidence_threshold: float = 0.5, cooldown_s: float = 5,
        sample_interval_s: float = 10, max_images: int = 100,
    ):
        self.directory = Path(directory)
        self.enabled = enabled
        self.confidence_threshold = confidence_threshold
        self.cooldown_ns = int(cooldown_s * 1e9)
        self.sample_interval_ns = int(sample_interval_s * 1e9)
        self.max_images = max_images
        self.saved_images = 0
        self.last_evidence_ns: int | None = None
        self.last_sample_ns: int | None = None
        self.seen_frames: set[str] = set()
        if enabled:
            (self.directory / "samples").mkdir(parents=True, exist_ok=True)

    def sample_frame(self, frame: FramePacket) -> bool:
        if not self._allowed(frame, self.last_sample_ns, self.sample_interval_ns):
            return False
        self._save_image(frame, f"sample_{frame.sequence:06d}.png")
        self.last_sample_ns = frame.capture_timestamp_monotonic_ns
        return True

    def record_detection(self, frame: FramePacket, result: DetectionResult) -> bool:
        candidates = [item for item in result.detections if item.confidence >= self.confidence_threshold]
        if not candidates or not self._allowed(frame, self.last_evidence_ns, self.cooldown_ns):
            return False
        remaining = self.max_images - self.saved_images
        if remaining < 2:
            return False
        rgb = to_rgb(frame.image, frame.pixel_format)
        annotated = Image.fromarray(rgb)
        draw = ImageDraw.Draw(annotated)
        crops_saved = 0
        for index, detection in enumerate(candidates):
            box = (detection.x1, detection.y1, detection.x2, detection.y2)
            draw.rectangle(box, outline=(255, 80, 40), width=2)
            draw.text((detection.x1, max(0, detection.y1 - 12)), f"person {detection.confidence:.2f}", fill=(255, 255, 255))
            if 2 + crops_saved < remaining:
                crop = Image.fromarray(rgb).crop(box)
                crop.save(self.directory / "samples" / f"evidence_{frame.sequence:06d}_{index}_crop.png")
                crops_saved += 1
        annotated.save(self.directory / "samples" / f"evidence_{frame.sequence:06d}_annotated.png")
        Image.fromarray(rgb).save(self.directory / "samples" / f"evidence_{frame.sequence:06d}_full.png")
        self.saved_images += 2 + crops_saved
        self.last_evidence_ns = frame.capture_timestamp_monotonic_ns
        self.seen_frames.add(frame.frame_id)
        return True

    def append_frame_metadata(self, frame: FramePacket, path: str | Path) -> None:
        _append_json(Path(path), frame.metadata_dict())

    def _allowed(self, frame: FramePacket, previous_ns: int | None, interval_ns: int) -> bool:
        return bool(
            self.enabled and self.saved_images < self.max_images
            and frame.frame_id not in self.seen_frames
            and (previous_ns is None or frame.capture_timestamp_monotonic_ns - previous_ns >= interval_ns)
        )

    def _save_image(self, frame: FramePacket, name: str) -> None:
        Image.fromarray(to_rgb(frame.image, frame.pixel_format)).save(self.directory / "samples" / name)
        self.saved_images += 1
        self.seen_frames.add(frame.frame_id)


def _append_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n")
