import type { ReplaySample } from "../types/domain";
import { quaternionSlerp, type Vec3 } from "./frames";

export const REPLAY_STALE_GAP_MS = 2_000;

export function interpolateReplaySample(samples: ReplaySample[], vehicleId: string, timestampMs: number): ReplaySample | null {
  const values = samples.filter((sample) => sample.vehicle_id === vehicleId).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  if (!values.length) return null;
  const afterIndex = values.findIndex((sample) => Date.parse(sample.timestamp) >= timestampMs);
  if (afterIndex <= 0) return Math.abs(Date.parse(values[0].timestamp) - timestampMs) <= REPLAY_STALE_GAP_MS ? values[0] : null;
  if (afterIndex < 0) return Math.abs(Date.parse(values.at(-1)!.timestamp) - timestampMs) <= REPLAY_STALE_GAP_MS ? values.at(-1)! : null;
  const before = values[afterIndex - 1]; const after = values[afterIndex];
  const start = Date.parse(before.timestamp); const end = Date.parse(after.timestamp);
  if (end - start > REPLAY_STALE_GAP_MS) return null;
  const fraction = end === start ? 0 : (timestampMs - start) / (end - start);
  const position = before.position_enu_m.map((value, index) => value + (after.position_enu_m[index] - value) * fraction) as Vec3;
  return {
    ...before,
    timestamp: new Date(timestampMs).toISOString(),
    position_enu_m: position,
    position_wgs84: {
      latitude: before.position_wgs84.latitude + (after.position_wgs84.latitude - before.position_wgs84.latitude) * fraction,
      longitude: before.position_wgs84.longitude + (after.position_wgs84.longitude - before.position_wgs84.longitude) * fraction,
      altitude_m: (before.position_wgs84.altitude_m ?? 0) + ((after.position_wgs84.altitude_m ?? 0) - (before.position_wgs84.altitude_m ?? 0)) * fraction,
    },
    quaternion_body_to_enu: quaternionSlerp(before.quaternion_body_to_enu, after.quaternion_body_to_enu, fraction),
  };
}

