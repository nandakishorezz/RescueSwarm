import { describe, expect, it } from "vitest";

import fixture from "../../../../data/fixtures/stage11s_spatial_geometry.json";
import type { CameraCalibrationView, CameraExtrinsicsView, ReplaySample } from "../types/domain";
import { cameraGroundFootprint, cameraRayToWorld, cameraWorldPose, intersectFlatGround, pixelToCameraRay } from "../spatial/camera";
import { bodyNedQuaternionToEnu, enuToNed, enuToThree, nedToEnu, quaternionSlerp, rotateVector, threeToEnu, type Quaternion, type Vec3 } from "../spatial/frames";
import { interpolateReplaySample } from "../spatial/replay";

const closeVector = (actual: number[], expected: number[], tolerance = 1e-8) => {
  expected.forEach((value, index) => expect(actual[index]).toBeCloseTo(value, Math.max(0, -Math.log10(tolerance))));
};

describe("central RescueSwarm spatial frames", () => {
  it("maps NED axes into ENU and maps ENU into Three without loss", () => {
    expect(nedToEnu([1, 0, 0])).toEqual([0, 1, -0]);
    expect(nedToEnu([0, 1, 0])).toEqual([1, 0, -0]);
    expect(nedToEnu([0, 0, 1])).toEqual([0, 0, -1]);
    closeVector(enuToNed(nedToEnu([3, 4, 5])), [3, 4, 5]);
    closeVector(threeToEnu(enuToThree([7, 8, 9])), [7, 8, 9]);
  });

  it("converts identity BODY→NED into the correct BODY→ENU rotation", () => {
    const quaternion = bodyNedQuaternionToEnu([1, 0, 0, 0]);
    closeVector(rotateVector(quaternion, [1, 0, 0]), [0, 1, 0]);
    closeVector(rotateVector(quaternion, [0, 1, 0]), [1, 0, 0]);
    closeVector(rotateVector(quaternion, [0, 0, 1]), [0, 0, -1]);
  });
});

describe("camera digital twin", () => {
  const calibration = fixture.calibration as CameraCalibrationView;
  const extrinsics = fixture.extrinsics as CameraExtrinsicsView;
  const bodyQuaternion = fixture.quaternion_body_to_enu as Quaternion;
  const bodyOrigin = fixture.vehicle_position_enu_m as Vec3;

  it("maps the principal point to CAMERA_OPTICAL +Z", () => {
    closeVector(pixelToCameraRay(calibration, fixture.pixel as [number, number]), fixture.expected_camera_ray);
  });

  it("matches the shared backend ray/ground fixture", () => {
    const pose = cameraWorldPose(bodyOrigin, bodyQuaternion, extrinsics);
    const ray = cameraRayToWorld(pose, pixelToCameraRay(calibration, fixture.pixel as [number, number]));
    closeVector(ray, fixture.expected_world_ray_enu);
    const hit = intersectFlatGround(pose.originEnuM, ray, fixture.ground_up_m);
    expect(hit).not.toBeNull();
    closeVector(hit!.pointEnuM, fixture.expected_intersection_enu_m);
    expect(hit!.rangeM).toBeCloseTo(fixture.expected_range_m, 8);
  });

  it("forms a four-corner nadir footprint and rejects above-horizon rays", () => {
    const footprint = cameraGroundFootprint(calibration, cameraWorldPose(bodyOrigin, bodyQuaternion, extrinsics));
    expect(footprint).toHaveLength(4);
    expect(intersectFlatGround([0, 0, 10], [1, 0, 0])).toBeNull();
    expect(intersectFlatGround([0, 0, 10], [0, 0, 1])).toBeNull();
  });
});

describe("deterministic replay interpolation", () => {
  it("uses position interpolation and quaternion SLERP but rejects stale gaps", () => {
    const base: ReplaySample = {
      timestamp: "2026-01-01T00:00:00.000Z", vehicle_id: "drone_01",
      position_wgs84: { latitude: 10, longitude: 20, altitude_m: 10 }, position_enu_m: [0, 0, 10],
      quaternion_body_to_enu: [1, 0, 0, 0], mode: "AUTO", mission_state: "SEARCHING", evidence_class: "SITL",
    };
    const next = { ...base, timestamp: "2026-01-01T00:00:01.000Z", position_enu_m: [10, 0, 10] as Vec3, quaternion_body_to_enu: [0, 0, 0, 1] as Quaternion };
    const middle = interpolateReplaySample([base, next], "drone_01", Date.parse("2026-01-01T00:00:00.500Z"));
    expect(middle?.position_enu_m[0]).toBeCloseTo(5);
    closeVector(middle!.quaternion_body_to_enu, quaternionSlerp(base.quaternion_body_to_enu, next.quaternion_body_to_enu, .5));
    expect(interpolateReplaySample([{ ...next, timestamp: "2026-01-01T00:00:05.000Z" }, base], "drone_01", Date.parse("2026-01-01T00:00:02.500Z"))).toBeNull();
  });
});

