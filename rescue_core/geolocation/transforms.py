"""Central coordinate and quaternion transforms.

Frames:
- CAMERA_OPTICAL: +x image-right, +y image-down, +z optical-forward.
- BODY_FRD (ArduPilot): +x forward, +y right, +z down.
- NED: +x North, +y East, +z Down.
- ENU: +x East, +y North, +z Up.

Quaternions are stored scalar-first ``[w, x, y, z]`` and represent active
rotations from the named source frame into the named destination frame.
"""

from __future__ import annotations

import math

import numpy as np


def normalize_vector(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Cannot normalize a zero or invalid vector")
    return value / norm


def normalize_quaternion(quaternion: np.ndarray | list[float]) -> np.ndarray:
    value = np.asarray(quaternion, dtype=float)
    if value.shape != (4,):
        raise ValueError("Quaternion must contain [w, x, y, z]")
    return normalize_vector(value)


def quaternion_conjugate(quaternion: np.ndarray | list[float]) -> np.ndarray:
    w, x, y, z = normalize_quaternion(quaternion)
    return np.array([w, -x, -y, -z], dtype=float)


def quaternion_multiply(first: np.ndarray | list[float], second: np.ndarray | list[float]) -> np.ndarray:
    aw, ax, ay, az = normalize_quaternion(first)
    bw, bx, by, bz = normalize_quaternion(second)
    return normalize_quaternion(np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]))


def quaternion_to_matrix(quaternion: np.ndarray | list[float]) -> np.ndarray:
    w, x, y, z = normalize_quaternion(quaternion)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def matrix_to_quaternion(matrix: np.ndarray) -> np.ndarray:
    rotation = np.asarray(matrix, dtype=float)
    if rotation.shape != (3, 3):
        raise ValueError("Rotation matrix must be 3x3")
    trace = float(np.trace(rotation))
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        quaternion = [0.25 * s, (rotation[2, 1] - rotation[1, 2]) / s,
                      (rotation[0, 2] - rotation[2, 0]) / s, (rotation[1, 0] - rotation[0, 1]) / s]
    else:
        index = int(np.argmax(np.diag(rotation)))
        if index == 0:
            s = math.sqrt(1 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2
            quaternion = [(rotation[2, 1] - rotation[1, 2]) / s, 0.25 * s,
                          (rotation[0, 1] + rotation[1, 0]) / s, (rotation[0, 2] + rotation[2, 0]) / s]
        elif index == 1:
            s = math.sqrt(1 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2
            quaternion = [(rotation[0, 2] - rotation[2, 0]) / s,
                          (rotation[0, 1] + rotation[1, 0]) / s, 0.25 * s,
                          (rotation[1, 2] + rotation[2, 1]) / s]
        else:
            s = math.sqrt(1 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2
            quaternion = [(rotation[1, 0] - rotation[0, 1]) / s,
                          (rotation[0, 2] + rotation[2, 0]) / s,
                          (rotation[1, 2] + rotation[2, 1]) / s, 0.25 * s]
    return normalize_quaternion(np.asarray(quaternion))


def quaternion_from_euler(roll_rad: float, pitch_rad: float, yaw_rad: float) -> np.ndarray:
    cr, sr = math.cos(roll_rad / 2), math.sin(roll_rad / 2)
    cp, sp = math.cos(pitch_rad / 2), math.sin(pitch_rad / 2)
    cy, sy = math.cos(yaw_rad / 2), math.sin(yaw_rad / 2)
    return normalize_quaternion(np.array([
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ]))


def rotate_vector(quaternion: np.ndarray | list[float], vector: np.ndarray | list[float]) -> np.ndarray:
    return quaternion_to_matrix(quaternion) @ np.asarray(vector, dtype=float)


def quaternion_slerp(first: np.ndarray | list[float], second: np.ndarray | list[float], fraction: float) -> np.ndarray:
    if not 0 <= fraction <= 1:
        raise ValueError("SLERP fraction must be within [0, 1]")
    q0, q1 = normalize_quaternion(first), normalize_quaternion(second)
    dot = float(np.dot(q0, q1))
    if dot < 0:
        q1, dot = -q1, -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return normalize_quaternion(q0 + fraction * (q1 - q0))
    angle = math.acos(dot)
    sine = math.sin(angle)
    return normalize_quaternion(math.sin((1 - fraction) * angle) / sine * q0 + math.sin(fraction * angle) / sine * q1)


def ned_to_enu(vector_ned: np.ndarray | list[float]) -> np.ndarray:
    north, east, down = np.asarray(vector_ned, dtype=float)
    return np.array([east, north, -down], dtype=float)


def enu_to_ned(vector_enu: np.ndarray | list[float]) -> np.ndarray:
    east, north, up = np.asarray(vector_enu, dtype=float)
    return np.array([north, east, -up], dtype=float)


NED_TO_ENU_MATRIX = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])


def body_to_enu_rotation(quaternion_body_to_ned: np.ndarray | list[float]) -> np.ndarray:
    return NED_TO_ENU_MATRIX @ quaternion_to_matrix(quaternion_body_to_ned)

