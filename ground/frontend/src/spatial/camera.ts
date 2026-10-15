import type { CameraCalibrationView, CameraExtrinsicsView } from "../types/domain";
import { add, normalizeVector, quaternionMultiply, rotateVector, scale, type Quaternion, type Vec3 } from "./frames";

export interface CameraPose { originEnuM: Vec3; quaternionCameraToEnu: Quaternion; }
export interface GroundIntersection { pointEnuM: Vec3; rangeM: number; incidenceAngleDeg: number; }

function undistortBrown(xd: number, yd: number, coefficients: number[]): [number, number] {
  const [k1 = 0, k2 = 0, p1 = 0, p2 = 0, k3 = 0, k4 = 0, k5 = 0, k6 = 0] = coefficients;
  let x = xd; let y = yd;
  for (let index = 0; index < 12; index += 1) {
    const r2 = x * x + y * y;
    const numerator = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2;
    const denominator = 1 + k4 * r2 + k5 * r2 * r2 + k6 * r2 * r2 * r2;
    const radial = numerator / denominator;
    const deltaX = 2 * p1 * x * y + p2 * (r2 + 2 * x * x);
    const deltaY = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y;
    x = (xd - deltaX) / radial;
    y = (yd - deltaY) / radial;
  }
  return [x, y];
}

function undistortFisheye(xd: number, yd: number, coefficients: number[]): [number, number] {
  const rd = Math.hypot(xd, yd);
  if (rd < 1e-12) return [0, 0];
  const [k1 = 0, k2 = 0, k3 = 0, k4 = 0] = coefficients;
  let theta = rd;
  for (let index = 0; index < 12; index += 1) {
    const t2 = theta * theta;
    const polynomial = 1 + k1 * t2 + k2 * t2 ** 2 + k3 * t2 ** 3 + k4 * t2 ** 4;
    const functionValue = theta * polynomial - rd;
    const derivative = 1 + 3 * k1 * t2 + 5 * k2 * t2 ** 2 + 7 * k3 * t2 ** 3 + 9 * k4 * t2 ** 4;
    theta -= functionValue / derivative;
  }
  const radius = Math.tan(theta);
  return [xd / rd * radius, yd / rd * radius];
}

export function pixelToCameraRay(calibration: CameraCalibrationView, pixel: [number, number]): Vec3 {
  const fx = calibration.camera_matrix[0][0]; const fy = calibration.camera_matrix[1][1];
  const cx = calibration.camera_matrix[0][2]; const cy = calibration.camera_matrix[1][2];
  const xd = (pixel[0] - cx) / fx; const yd = (pixel[1] - cy) / fy;
  const [x, y] = calibration.distortion_model === "FISHEYE"
    ? undistortFisheye(xd, yd, calibration.distortion_coefficients)
    : undistortBrown(xd, yd, calibration.distortion_coefficients);
  return normalizeVector([x, y, 1]);
}

export function cameraWorldPose(bodyOriginEnuM: Vec3, quaternionBodyToEnu: Quaternion, extrinsics: CameraExtrinsicsView): CameraPose {
  return {
    originEnuM: add(bodyOriginEnuM, rotateVector(quaternionBodyToEnu, extrinsics.translation_body_m)),
    quaternionCameraToEnu: quaternionMultiply(quaternionBodyToEnu, extrinsics.quaternion_camera_to_body),
  };
}

export function cameraRayToWorld(cameraPose: CameraPose, cameraRay: Vec3): Vec3 {
  return normalizeVector(rotateVector(cameraPose.quaternionCameraToEnu, cameraRay));
}

export function intersectFlatGround(originEnuM: Vec3, rayEnu: Vec3, groundUpM = 0): GroundIntersection | null {
  const ray = normalizeVector(rayEnu);
  if (ray[2] >= -1e-6) return null;
  const rangeM = (groundUpM - originEnuM[2]) / ray[2];
  if (!Number.isFinite(rangeM) || rangeM <= 0 || rangeM > 10_000) return null;
  const point = add(originEnuM, scale(ray, rangeM)); point[2] = groundUpM;
  return { pointEnuM: point, rangeM, incidenceAngleDeg: Math.acos(Math.min(1, Math.max(-1, -ray[2]))) * 180 / Math.PI };
}

export function cameraFrustum(calibration: CameraCalibrationView, pose: CameraPose, farM: number): { camera: Vec3[]; world: Vec3[] } {
  const pixels: [number, number][] = [[0, 0], [calibration.image_width, 0], [calibration.image_width, calibration.image_height], [0, calibration.image_height]];
  const camera = pixels.map((pixel) => scale(pixelToCameraRay(calibration, pixel), farM));
  const world = camera.map((point) => add(pose.originEnuM, rotateVector(pose.quaternionCameraToEnu, point)));
  return { camera, world };
}

export function cameraGroundFootprint(calibration: CameraCalibrationView, pose: CameraPose, groundUpM = 0): GroundIntersection[] {
  const pixels: [number, number][] = [[0, 0], [calibration.image_width, 0], [calibration.image_width, calibration.image_height], [0, calibration.image_height]];
  return pixels.map((pixel) => intersectFlatGround(pose.originEnuM, cameraRayToWorld(pose, pixelToCameraRay(calibration, pixel)), groundUpM))
    .filter((value): value is GroundIntersection => value !== null);
}

