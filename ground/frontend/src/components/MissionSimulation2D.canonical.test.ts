import { describe, expect, it } from "vitest";

import type { Target } from "../types/domain";
import { canonicalSurvivorFeatures } from "./MissionSimulation2D";

const target = (targetId: string, state: Target["state"] = "CONFIRMED"): Target => ({
  target_id: targetId, mission_id: "mission-a", state,
  latitude: 12.972, longitude: 77.592, east_m: 1, north_m: 2,
  detection_confidence: .8, geolocation_uncertainty_m: 2,
  observation_count: 3, first_seen_at: "2026-10-13T10:00:00Z",
  last_seen_at: "2026-10-13T10:00:01Z", delivery_state: "PENDING", simulated: true,
});

describe("Mission MapLibre autonomous survivor layer", () => {
  it("uses only canonical confirmed IDs, independent of scenario truth", () => {
    const features = canonicalSurvivorFeatures({
      a: target("person-a"), b: target("person-b"),
      candidate: target("candidate", "CANDIDATE"), alias: target("alias", "REJECTED"),
    });
    expect(features.features.map((item) => item.properties.target_id)).toEqual(["person-a", "person-b"]);
    expect(features.features[0].geometry.coordinates).toEqual([77.592, 12.972]);
  });

  it("replaces a location update by ID and clears on a new session", () => {
    const moved = { ...target("person-a"), latitude: 12.973, observation_count: 10 };
    const features = canonicalSurvivorFeatures({ a: moved });
    expect(features.features).toHaveLength(1);
    expect(features.features[0].geometry.coordinates).toEqual([77.592, 12.973]);
    expect(canonicalSurvivorFeatures({}).features).toHaveLength(0);
  });
});
