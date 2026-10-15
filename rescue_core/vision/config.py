"""Validated Stage 4 configuration loaded independently from the GCS."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class CameraProfile(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    pixel_format: str
    verification: str = "UNVERIFIED_DEFAULT"


class CameraSettings(BaseModel):
    source: Literal["picamera2", "video", "image_directory", "synthetic"]
    source_id: str
    profile: str
    camera_index: int = Field(default=0, ge=0, le=16)
    profiles: dict[str, CameraProfile]
    no_frame_timeout_s: float = Field(gt=0)
    restart_attempts: int = Field(ge=0, le=20)
    restart_backoff_s: float = Field(gt=0)

    @model_validator(mode="after")
    def selected_profile_exists(self) -> "CameraSettings":
        if self.profile not in self.profiles:
            raise ValueError(f"Unknown camera profile: {self.profile}")
        return self


class DetectorSettings(BaseModel):
    backend: Literal["mock", "onnx", "ncnn"]
    model_id: str
    confidence_threshold: float = Field(ge=0, le=1)
    person_class_ids: list[int]
    input_width: int = Field(gt=0)
    input_height: int = Field(gt=0)
    strategy: Literal["full_frame", "tiled"]
    normalization: Literal["none", "zero_to_one"]
    tile_width: int = Field(gt=0)
    tile_height: int = Field(gt=0)
    tile_overlap: float = Field(ge=0, lt=0.9)
    nms_iou_threshold: float = Field(gt=0, le=1)


class QueueSettings(BaseModel):
    size: int = Field(ge=1, le=100)
    drop_policy: Literal["drop_oldest"]


class BenchmarkSettings(BaseModel):
    warmup_frames: int = Field(ge=0, le=10_000)
    duration_s: float = Field(gt=0)
    resource_sample_interval_s: float = Field(gt=0)


class EvidenceSettings(BaseModel):
    enabled: bool
    confidence_threshold: float = Field(ge=0, le=1)
    cooldown_s: float = Field(ge=0)
    sample_interval_s: float = Field(ge=0)
    max_images: int = Field(ge=0, le=100_000)


class RuntimeSettings(BaseModel):
    event_output: Path
    run_root: Path
    model_manifest: Path


class VisionConfig(BaseModel):
    application: dict[str, object]
    camera: CameraSettings
    detector: DetectorSettings
    queue: QueueSettings
    benchmark: BenchmarkSettings
    evidence: EvidenceSettings
    runtime: RuntimeSettings


def load_vision_config(path: str | Path = "config/vision.yaml") -> VisionConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Vision configuration root must be a mapping")
    return VisionConfig.model_validate(raw)
