"""Persistent, provenance-rich camera intrinsics and fixed mount extrinsics."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, Field, model_validator

from .transforms import matrix_to_quaternion, quaternion_from_euler, quaternion_to_matrix


class DistortionModel(StrEnum):
    PINHOLE_BROWN = "PINHOLE_BROWN"
    FISHEYE = "FISHEYE"


class CalibrationVerdict(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    NOT_TESTED = "NOT_TESTED"


class ExtrinsicsMethod(StrEnum):
    MANUAL_MEASUREMENT = "MANUAL_MEASUREMENT"
    CALIBRATED = "CALIBRATED"
    APPROXIMATE_NADIR = "APPROXIMATE_NADIR"
    UNKNOWN = "UNKNOWN"


class ExtrinsicsQuality(StrEnum):
    CALIBRATED = "CALIBRATED"
    APPROXIMATE = "APPROXIMATE"
    UNKNOWN = "UNKNOWN"


class CameraCalibration(BaseModel):
    calibration_id: str
    camera_identifier: str | None = None
    sensor: str
    resolution: tuple[int, int]
    sensor_mode: str
    distortion_model: DistortionModel
    camera_matrix: list[list[float]]
    distortion_coefficients: list[float]
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    rms_reprojection_error: float = Field(ge=0)
    mean_reprojection_error: float = Field(ge=0)
    median_reprojection_error: float = Field(ge=0)
    p95_reprojection_error: float = Field(ge=0)
    per_frame_errors: list[float] = Field(default_factory=list)
    pattern_type: str
    pattern_dimensions: tuple[int, int]
    square_size_m: float = Field(gt=0)
    images_used: int = Field(ge=0)
    images_rejected: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    software_version: str
    calibration_version: str = "1"
    verdict: CalibrationVerdict
    validation_reasons: list[str] = Field(default_factory=list)
    sha256: str | None = None

    @model_validator(mode="after")
    def validate_matrix_and_resolution(self) -> "CameraCalibration":
        matrix = np.asarray(self.camera_matrix, dtype=float)
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError("camera_matrix must be a finite 3x3 matrix")
        if matrix[0, 0] <= 0 or matrix[1, 1] <= 0:
            raise ValueError("camera focal lengths must be positive")
        if tuple(self.resolution) != (self.image_width, self.image_height):
            raise ValueError("resolution must match image_width/image_height")
        expected = 4 if self.distortion_model is DistortionModel.FISHEYE else 4
        if len(self.distortion_coefficients) < expected:
            raise ValueError(f"{self.distortion_model} requires at least {expected} distortion coefficients")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"sha256"})
        return payload

    def computed_sha256(self) -> str:
        canonical = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_hash(self) -> "CameraCalibration":
        return self.model_copy(update={"sha256": self.computed_sha256()})

    def verify_hash(self) -> None:
        if not self.sha256 or self.sha256 != self.computed_sha256():
            raise ValueError(f"Calibration hash mismatch: {self.calibration_id}")

    def assert_compatible(self, width: int, height: int, sensor_mode: str) -> None:
        if (width, height) != (self.image_width, self.image_height) or sensor_mode != self.sensor_mode:
            raise ValueError(
                "CALIBRATION_MISMATCH: active resolution/sensor mode "
                f"{width}x{height}/{sensor_mode} != calibrated "
                f"{self.image_width}x{self.image_height}/{self.sensor_mode}"
            )


class CameraExtrinsics(BaseModel):
    extrinsics_id: str
    camera_identifier: str | None = None
    mount_type: str = "FIXED"
    translation_body_m: tuple[float, float, float]
    quaternion_camera_to_body: tuple[float, float, float, float]
    rotation_camera_to_body: list[list[float]]
    method: ExtrinsicsMethod
    quality: ExtrinsicsQuality
    rotation_uncertainty_deg: float | None = Field(default=None, ge=0)
    translation_uncertainty_m: float | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = None
    sha256: str | None = None

    @model_validator(mode="after")
    def validate_rotation(self) -> "CameraExtrinsics":
        matrix = np.asarray(self.rotation_camera_to_body, dtype=float)
        quaternion_matrix = quaternion_to_matrix(self.quaternion_camera_to_body)
        if matrix.shape != (3, 3) or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-6):
            raise ValueError("rotation_camera_to_body must be an orthonormal 3x3 matrix")
        if np.linalg.det(matrix) < 0.999999:
            raise ValueError("rotation_camera_to_body must be a proper rotation")
        if not np.allclose(matrix, quaternion_matrix, atol=1e-6):
            raise ValueError("Extrinsics quaternion and rotation matrix disagree")
        return self

    @classmethod
    def from_euler_degrees(
        cls, *, extrinsics_id: str, roll_deg: float, pitch_deg: float, yaw_deg: float,
        translation_body_m: tuple[float, float, float], method: ExtrinsicsMethod,
        quality: ExtrinsicsQuality, camera_identifier: str | None = None,
        rotation_uncertainty_deg: float | None = None,
        translation_uncertainty_m: float | None = None, notes: str | None = None,
    ) -> "CameraExtrinsics":
        quaternion = quaternion_from_euler(*(math.radians(value) for value in (roll_deg, pitch_deg, yaw_deg)))
        matrix = quaternion_to_matrix(quaternion)
        result = cls(
            extrinsics_id=extrinsics_id, camera_identifier=camera_identifier,
            translation_body_m=translation_body_m,
            quaternion_camera_to_body=tuple(float(value) for value in quaternion),
            rotation_camera_to_body=matrix.tolist(), method=method, quality=quality,
            rotation_uncertainty_deg=rotation_uncertainty_deg,
            translation_uncertainty_m=translation_uncertainty_m, notes=notes,
        )
        return result.with_hash()

    @classmethod
    def from_rotation_matrix(cls, **values: Any) -> "CameraExtrinsics":
        matrix = np.asarray(values.pop("rotation_camera_to_body"), dtype=float)
        return cls(
            rotation_camera_to_body=matrix.tolist(),
            quaternion_camera_to_body=tuple(float(value) for value in matrix_to_quaternion(matrix)),
            **values,
        ).with_hash()

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"sha256"})

    def computed_sha256(self) -> str:
        canonical = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_hash(self) -> "CameraExtrinsics":
        return self.model_copy(update={"sha256": self.computed_sha256()})

    def verify_hash(self) -> None:
        if not self.sha256 or self.sha256 != self.computed_sha256():
            raise ValueError(f"Extrinsics hash mismatch: {self.extrinsics_id}")


class CalibrationRegistry:
    """Filesystem registry; physical selection is always explicit by ID."""

    def __init__(self, root: str | Path = "calibration/cameras"):
        self.root = Path(root)

    def directory(self, calibration_id: str) -> Path:
        if not calibration_id or Path(calibration_id).name != calibration_id:
            raise ValueError("Calibration ID must be a single safe path component")
        return self.root / calibration_id

    def save(
        self, calibration: CameraCalibration, extrinsics: CameraExtrinsics | None,
        validation: dict[str, Any] | None = None,
    ) -> Path:
        directory = self.directory(calibration.calibration_id)
        directory.mkdir(parents=True, exist_ok=False)
        hashed = calibration.with_hash()
        (directory / "intrinsics.yaml").write_text(
            yaml.safe_dump(hashed.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
        )
        if extrinsics:
            (directory / "extrinsics.yaml").write_text(
                yaml.safe_dump(extrinsics.with_hash().model_dump(mode="json"), sort_keys=False), encoding="utf-8"
            )
        (directory / "validation.json").write_text(
            json.dumps(validation or {"verdict": hashed.verdict, "reasons": hashed.validation_reasons}, indent=2),
            encoding="utf-8",
        )
        manifest = {
            "calibration_id": hashed.calibration_id,
            "intrinsics_sha256": hashed.sha256,
            "extrinsics_id": extrinsics.extrinsics_id if extrinsics else None,
            "extrinsics_sha256": extrinsics.with_hash().sha256 if extrinsics else None,
            "resolution": list(hashed.resolution), "sensor_mode": hashed.sensor_mode,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return directory

    def load_intrinsics(self, calibration_id: str) -> CameraCalibration:
        path = self.directory(calibration_id) / "intrinsics.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"Calibration not found: {calibration_id}")
        model = CameraCalibration.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        model.verify_hash()
        return model

    def load_extrinsics(self, calibration_id: str) -> CameraExtrinsics:
        path = self.directory(calibration_id) / "extrinsics.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"Extrinsics not found for calibration: {calibration_id}")
        model = CameraExtrinsics.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        model.verify_hash()
        return model

