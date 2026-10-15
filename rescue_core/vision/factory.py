"""Construct configured local sources and detectors without implicit provisioning."""

from __future__ import annotations

from pathlib import Path

from .camera import (
    CameraSource, ImageDirectoryCameraSource, Picamera2CameraSource,
    SyntheticCameraSource, VideoFileCameraSource,
)
from .config import VisionConfig
from .detectors import DetectorBackend, MockDetectorBackend, NCNNDetectorBackend, ONNXDetectorBackend
from .manifest import load_model_manifest, validate_model_artifact
from .preprocess import VisionPreprocessor


def create_camera_source(
    config: VisionConfig, source_type: str | None = None,
    input_path: str | Path | None = None, loop: bool = False,
) -> CameraSource:
    selected = source_type or config.camera.source
    profile = config.camera.profiles[config.camera.profile]
    if selected == "synthetic":
        return SyntheticCameraSource(config.camera.source_id, profile.width, profile.height, profile.fps)
    if selected == "image_directory":
        if not input_path:
            raise ValueError("Image-directory source requires --input")
        return ImageDirectoryCameraSource(input_path, config.camera.source_id, loop)
    if selected == "video":
        if not input_path:
            raise ValueError("Video source requires --input")
        return VideoFileCameraSource(input_path, config.camera.source_id, loop)
    if selected == "picamera2":
        return Picamera2CameraSource(
            config.camera.source_id, profile.width, profile.height,
            profile.fps, profile.pixel_format, config.camera.camera_index,
        )
    raise ValueError(f"Unknown camera source: {selected}")


def create_detector(config: VisionConfig, backend: str | None = None, model_id: str | None = None) -> DetectorBackend:
    manifest_path = config.runtime.model_manifest
    manifest = load_model_manifest(manifest_path)
    model = manifest.require(model_id or config.detector.model_id)
    selected_backend = backend or config.detector.backend
    if selected_backend != model.backend:
        raise ValueError(f"Model '{model.id}' is for backend '{model.backend}', not '{selected_backend}'")
    artifact = validate_model_artifact(model, manifest_path)
    preprocessor = VisionPreprocessor(
        config.detector.input_width, config.detector.input_height,
        config.detector.normalization, config.detector.strategy,
        config.detector.tile_width, config.detector.tile_height, config.detector.tile_overlap,
    )
    common = dict(
        model=model, preprocessor=preprocessor,
        confidence_threshold=config.detector.confidence_threshold,
        person_class_ids=set(config.detector.person_class_ids),
        nms_iou_threshold=config.detector.nms_iou_threshold,
    )
    if selected_backend == "mock":
        return MockDetectorBackend(**common)
    if selected_backend == "onnx":
        assert artifact is not None
        return ONNXDetectorBackend(artifact, **common)
    if selected_backend == "ncnn":
        assert artifact is not None
        return NCNNDetectorBackend(artifact, **common)
    raise ValueError(f"Unsupported detector backend: {selected_backend}")
