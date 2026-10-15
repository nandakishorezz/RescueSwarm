"""Checkerboard calibration and held-out validation using local OpenCV only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .calibration import CalibrationVerdict, CameraCalibration, DistortionModel


@dataclass(frozen=True)
class CalibrationFrame:
    path: Path
    object_points: np.ndarray
    image_points: np.ndarray


def checkerboard_object_points(pattern_dimensions: tuple[int, int], square_size_m: float) -> np.ndarray:
    columns, rows = pattern_dimensions
    points = np.zeros((columns * rows, 3), np.float32)
    points[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * square_size_m
    return points


def detect_checkerboards(
    image_paths: list[Path], pattern_dimensions: tuple[int, int], square_size_m: float,
) -> tuple[list[CalibrationFrame], list[dict[str, str]], tuple[int, int]]:
    accepted: list[CalibrationFrame] = []
    rejected: list[dict[str, str]] = []
    image_size: tuple[int, int] | None = None
    object_points = checkerboard_object_points(pattern_dimensions, square_size_m)
    for path in image_paths:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            rejected.append({"path": str(path), "reason": "IMAGE_READ_FAILED"})
            continue
        size = (image.shape[1], image.shape[0])
        if image_size is None:
            image_size = size
        elif size != image_size:
            rejected.append({"path": str(path), "reason": "RESOLUTION_MISMATCH"})
            continue
        sharpness = float(cv2.Laplacian(image, cv2.CV_64F).var())
        if sharpness < 20:
            rejected.append({"path": str(path), "reason": "BLURRED_FRAME"})
            continue
        found, corners = cv2.findChessboardCornersSB(
            image, pattern_dimensions, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
        )
        if not found or corners is None:
            rejected.append({"path": str(path), "reason": "PATTERN_NOT_FULLY_VISIBLE"})
            continue
        normalized_center = np.mean(corners.reshape(-1, 2), axis=0) / np.asarray(size)
        normalized_area = cv2.contourArea(cv2.convexHull(corners.astype(np.float32))) / (size[0] * size[1])
        duplicate = any(
            np.linalg.norm(normalized_center - np.mean(frame.image_points.reshape(-1, 2), axis=0) / np.asarray(size)) < 0.025
            and abs(normalized_area - cv2.contourArea(cv2.convexHull(frame.image_points.astype(np.float32))) / (size[0] * size[1])) < 0.02
            for frame in accepted
        )
        if duplicate:
            rejected.append({"path": str(path), "reason": "NEAR_DUPLICATE_PATTERN_POSE"})
            continue
        accepted.append(CalibrationFrame(path, object_points.copy(), corners.reshape(-1, 2)))
    if image_size is None:
        raise ValueError("No readable calibration images")
    return accepted, rejected, image_size


def _reprojection_errors(
    frames: list[CalibrationFrame], matrix: np.ndarray, distortion: np.ndarray,
    rotations: list[np.ndarray], translations: list[np.ndarray], model: DistortionModel,
) -> list[float]:
    errors: list[float] = []
    for frame, rotation, translation in zip(frames, rotations, translations, strict=True):
        if model is DistortionModel.FISHEYE:
            projected, _ = cv2.fisheye.projectPoints(
                frame.object_points.reshape(1, -1, 3).astype(np.float64), rotation, translation, matrix, distortion
            )
        else:
            projected, _ = cv2.projectPoints(frame.object_points, rotation, translation, matrix, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - frame.image_points) ** 2, axis=1)))))
    return errors


def calibrate_checkerboard(
    *, calibration_id: str, sensor: str, sensor_mode: str,
    image_paths: list[Path], pattern_dimensions: tuple[int, int], square_size_m: float,
    distortion_model: DistortionModel, camera_identifier: str | None = None,
    warning_rms_px: float = 1.0, fail_rms_px: float = 2.0, fail_p95_px: float = 3.0,
) -> tuple[CameraCalibration, dict[str, object]]:
    frames, rejected, image_size = detect_checkerboards(image_paths, pattern_dimensions, square_size_m)
    if len(frames) < 8:
        raise ValueError(f"At least 8 diverse checkerboard images are required; accepted {len(frames)}")
    objects = [frame.object_points for frame in frames]
    images = [frame.image_points for frame in frames]
    if distortion_model is DistortionModel.FISHEYE:
        objects_fish = [value.reshape(1, -1, 3).astype(np.float64) for value in objects]
        images_fish = [value.reshape(1, -1, 2).astype(np.float64) for value in images]
        matrix = np.eye(3, dtype=np.float64)
        distortion = np.zeros((4, 1), dtype=np.float64)
        rms, matrix, distortion, rotations, translations = cv2.fisheye.calibrate(
            objects_fish, images_fish, image_size, matrix, distortion, None, None,
            flags=cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC | cv2.fisheye.CALIB_CHECK_COND,
            criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-7),
        )
    else:
        rms, matrix, distortion, rotations, translations = cv2.calibrateCamera(
            objects, images, image_size, None, None
        )
    errors = _reprojection_errors(frames, matrix, distortion, rotations, translations, distortion_model)
    mean, median = float(np.mean(errors)), float(np.median(errors))
    p95 = float(np.percentile(errors, 95))
    reasons: list[str] = []
    verdict = CalibrationVerdict.PASS
    if rms > fail_rms_px or p95 > fail_p95_px:
        verdict = CalibrationVerdict.FAIL
        reasons.append("Reprojection error exceeds configured failure threshold")
    elif rms > warning_rms_px:
        verdict = CalibrationVerdict.WARNING
        reasons.append("RMS reprojection error exceeds configured warning threshold")
    calibration = CameraCalibration(
        calibration_id=calibration_id, camera_identifier=camera_identifier, sensor=sensor,
        resolution=image_size, sensor_mode=sensor_mode, distortion_model=distortion_model,
        camera_matrix=matrix.tolist(), distortion_coefficients=distortion.reshape(-1).tolist(),
        image_width=image_size[0], image_height=image_size[1], rms_reprojection_error=float(rms),
        mean_reprojection_error=mean, median_reprojection_error=median, p95_reprojection_error=p95,
        per_frame_errors=errors, pattern_type="CHECKERBOARD", pattern_dimensions=pattern_dimensions,
        square_size_m=square_size_m, images_used=len(frames), images_rejected=len(rejected),
        software_version=f"OpenCV {cv2.__version__}", verdict=verdict,
        validation_reasons=reasons,
    ).with_hash()
    evidence = {
        "verdict": verdict, "reasons": reasons, "accepted_images": [str(frame.path) for frame in frames],
        "rejected_images": rejected, "per_frame_errors_px": dict(zip((str(frame.path) for frame in frames), errors, strict=True)),
        "thresholds_px": {"warning_rms": warning_rms_px, "fail_rms": fail_rms_px, "fail_p95": fail_p95_px},
    }
    return calibration, evidence


def validate_calibration(
    calibration: CameraCalibration, image_paths: list[Path], output_directory: Path,
) -> dict[str, object]:
    frames, rejected, image_size = detect_checkerboards(
        image_paths, calibration.pattern_dimensions, calibration.square_size_m
    )
    calibration.assert_compatible(*image_size, calibration.sensor_mode)
    output_directory.mkdir(parents=True, exist_ok=True)
    matrix = np.asarray(calibration.camera_matrix, dtype=float)
    distortion = np.asarray(calibration.distortion_coefficients, dtype=float)
    errors: list[float] = []
    for index, frame in enumerate(frames):
        if calibration.distortion_model is DistortionModel.FISHEYE:
            normalized = cv2.fisheye.undistortPoints(
                frame.image_points.reshape(1, -1, 2).astype(np.float64), matrix, distortion[:4]
            ).reshape(-1, 2)
            success, rotation, translation = cv2.solvePnP(
                frame.object_points, normalized, np.eye(3), np.zeros(4)
            )
        else:
            success, rotation, translation = cv2.solvePnP(
                frame.object_points, frame.image_points, matrix, distortion
            )
        if not success:
            rejected.append({"path": str(frame.path), "reason": "VALIDATION_POSE_FAILED"})
            continue
        if calibration.distortion_model is DistortionModel.FISHEYE:
            projected, _ = cv2.fisheye.projectPoints(
                frame.object_points.reshape(1, -1, 3).astype(np.float64),
                rotation, translation, matrix, distortion[:4],
            )
        else:
            projected, _ = cv2.projectPoints(frame.object_points, rotation, translation, matrix, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - frame.image_points) ** 2, axis=1)))))
        image = cv2.imread(str(frame.path))
        if calibration.distortion_model is DistortionModel.FISHEYE:
            preview = cv2.fisheye.undistortImage(image, matrix, distortion[:4], Knew=matrix)
        else:
            preview = cv2.undistort(image, matrix, distortion)
        cv2.imwrite(str(output_directory / f"undistorted_{index:03d}.png"), preview)
    return {
        "status": "MEASURED" if errors else "NOT_MEASURED",
        "held_out_images": len(errors), "rejected_images": rejected,
        "mean_reprojection_error_px": float(np.mean(errors)) if errors else None,
        "median_reprojection_error_px": float(np.median(errors)) if errors else None,
        "p95_reprojection_error_px": float(np.percentile(errors, 95)) if errors else None,
        "per_frame_errors_px": errors,
    }
