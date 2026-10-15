/**
 * RescueSwarm's only frontend frame-conversion module.
 *
 * CAMERA_OPTICAL: +x image-right, +y image-down, +z optical-forward.
 * BODY_FRD:       +x forward, +y right, +z down.
 * NED:            +x north, +y east, +z down.
 * ENU:            +x east, +y north, +z up.
 * THREE:          +x east, +y up, -z north (right-handed display frame).
 *
 * Quaternions are scalar-first [w,x,y,z] active source-to-destination rotations.
 */

export type Vec3 = [number, number, number];
export type Quaternion = [number, number, number, number];
export type Matrix3 = [Vec3, Vec3, Vec3];

const EPSILON = 1e-12;

export function normalizeVector(value: Vec3): Vec3 {
  const length = Math.hypot(...value);
  if (!Number.isFinite(length) || length <= EPSILON) throw new Error("Cannot normalize zero/invalid vector");
  return value.map((item) => item / length) as Vec3;
}

export function normalizeQuaternion(value: Quaternion): Quaternion {
  const length = Math.hypot(...value);
  if (!Number.isFinite(length) || length <= EPSILON) throw new Error("Cannot normalize zero/invalid quaternion");
  const result = value.map((item) => item / length) as Quaternion;
  return result[0] < 0 ? result.map((item) => -item) as Quaternion : result;
}

export function quaternionMultiply(first: Quaternion, second: Quaternion): Quaternion {
  const [aw, ax, ay, az] = normalizeQuaternion(first);
  const [bw, bx, by, bz] = normalizeQuaternion(second);
  return normalizeQuaternion([
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ]);
}

export function quaternionToMatrix(value: Quaternion): Matrix3 {
  const [w, x, y, z] = normalizeQuaternion(value);
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
  ];
}

export function matrixToQuaternion(matrix: Matrix3): Quaternion {
  const trace = matrix[0][0] + matrix[1][1] + matrix[2][2];
  let value: Quaternion;
  if (trace > 0) {
    const s = Math.sqrt(trace + 1) * 2;
    value = [0.25 * s, (matrix[2][1] - matrix[1][2]) / s, (matrix[0][2] - matrix[2][0]) / s, (matrix[1][0] - matrix[0][1]) / s];
  } else if (matrix[0][0] > matrix[1][1] && matrix[0][0] > matrix[2][2]) {
    const s = Math.sqrt(1 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2;
    value = [(matrix[2][1] - matrix[1][2]) / s, 0.25 * s, (matrix[0][1] + matrix[1][0]) / s, (matrix[0][2] + matrix[2][0]) / s];
  } else if (matrix[1][1] > matrix[2][2]) {
    const s = Math.sqrt(1 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2;
    value = [(matrix[0][2] - matrix[2][0]) / s, (matrix[0][1] + matrix[1][0]) / s, 0.25 * s, (matrix[1][2] + matrix[2][1]) / s];
  } else {
    const s = Math.sqrt(1 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2;
    value = [(matrix[1][0] - matrix[0][1]) / s, (matrix[0][2] + matrix[2][0]) / s, (matrix[1][2] + matrix[2][1]) / s, 0.25 * s];
  }
  return normalizeQuaternion(value);
}

export function multiplyMatrixVector(matrix: Matrix3, vector: Vec3): Vec3 {
  return matrix.map((row) => row[0] * vector[0] + row[1] * vector[1] + row[2] * vector[2]) as Vec3;
}

export function multiplyMatrices(a: Matrix3, b: Matrix3): Matrix3 {
  return a.map((row) => [0, 1, 2].map((column) => row[0] * b[0][column] + row[1] * b[1][column] + row[2] * b[2][column]) as Vec3) as Matrix3;
}

export function rotateVector(quaternion: Quaternion, vector: Vec3): Vec3 {
  return multiplyMatrixVector(quaternionToMatrix(quaternion), vector);
}

export const NED_TO_ENU: Matrix3 = [[0, 1, 0], [1, 0, 0], [0, 0, -1]];
export const ENU_TO_THREE: Matrix3 = [[1, 0, 0], [0, 0, 1], [0, -1, 0]];
export const MODEL_TO_BODY_FRD: Matrix3 = [[1, 0, 0], [0, 0, 1], [0, -1, 0]];
export const NED_TO_ENU_QUATERNION = matrixToQuaternion(NED_TO_ENU);

export function nedToEnu([north, east, down]: Vec3): Vec3 { return [east, north, -down]; }
export function enuToNed([east, north, up]: Vec3): Vec3 { return [north, east, -up]; }
export function enuToThree([east, north, up]: Vec3): Vec3 { return [east, up, -north]; }
export function threeToEnu([x, y, z]: Vec3): Vec3 { return [x, -z, y]; }

export function bodyNedQuaternionToEnu(value: Quaternion): Quaternion {
  return quaternionMultiply(NED_TO_ENU_QUATERNION, value);
}

export function bodyQuaternionToThreeRotation(value: Quaternion): Matrix3 {
  return multiplyMatrices(ENU_TO_THREE, multiplyMatrices(quaternionToMatrix(value), MODEL_TO_BODY_FRD));
}

export function quaternionSlerp(first: Quaternion, second: Quaternion, fraction: number): Quaternion {
  if (fraction < 0 || fraction > 1) throw new Error("SLERP fraction must be within [0,1]");
  const q0 = normalizeQuaternion(first);
  let q1 = normalizeQuaternion(second);
  let dot = q0.reduce((sum, value, index) => sum + value * q1[index], 0);
  if (dot < 0) { q1 = q1.map((item) => -item) as Quaternion; dot = -dot; }
  dot = Math.min(1, Math.max(-1, dot));
  if (dot > 0.9995) return normalizeQuaternion(q0.map((value, index) => value + fraction * (q1[index] - value)) as Quaternion);
  const angle = Math.acos(dot);
  const sine = Math.sin(angle);
  return normalizeQuaternion(q0.map((value, index) => Math.sin((1 - fraction) * angle) / sine * value + Math.sin(fraction * angle) / sine * q1[index]) as Quaternion);
}

export function add(a: Vec3, b: Vec3): Vec3 { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
export function subtract(a: Vec3, b: Vec3): Vec3 { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
export function scale(vector: Vec3, factor: number): Vec3 { return vector.map((item) => item * factor) as Vec3; }

/** Local display conversion only; backend pyproj remains authoritative. */
export function enuOffsetToWgs84(origin: { latitude: number; longitude: number }, [east, north]: Vec3): [number, number] {
  const earthRadiusM = 6378137;
  const latitude = origin.latitude + north / earthRadiusM * 180 / Math.PI;
  const longitude = origin.longitude + east / (earthRadiusM * Math.cos(origin.latitude * Math.PI / 180)) * 180 / Math.PI;
  return [longitude, latitude];
}
