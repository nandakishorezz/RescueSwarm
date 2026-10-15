"""Pixel-only simulation perception adapters and evaluator components."""

from .camera_source import (
    CameraSensorRealismMetrics,
    GazeboCameraSource,
    GazeboFrameTransportMetrics,
    SensorRealismCameraSource,
)
from .pipeline import PixelPerceptionPipeline, PixelPerceptionMetrics, simulation_camera_geometry

__all__ = [
    "CameraSensorRealismMetrics", "GazeboCameraSource", "GazeboFrameTransportMetrics",
    "SensorRealismCameraSource", "PixelPerceptionPipeline", "PixelPerceptionMetrics",
    "simulation_camera_geometry",
]
