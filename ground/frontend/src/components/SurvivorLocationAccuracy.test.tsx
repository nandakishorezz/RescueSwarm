import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Target } from "../types/domain";
import {
  horizontalDistanceM,
  matchSurvivorLocations,
  SurvivorLocationAccuracy,
  type SimulationTruthSurvivor,
} from "./SurvivorLocationAccuracy";

const truth: SimulationTruthSurvivor[] = [
  { survivor_id: "S1", enabled: true, position_wgs84: [12.972, 77.592, 0] },
  { survivor_id: "S2", enabled: true, position_wgs84: [12.973, 77.593, 0] },
];

const target = (id: string, latitude: number, longitude: number): Target => ({
  target_id: id, mission_id: "mission-a", state: "CONFIRMED", latitude, longitude,
  east_m: 0, north_m: 0, detection_confidence: .8, geolocation_uncertainty_m: 2,
  observation_count: 4, first_seen_at: "2026-10-12T00:00:00Z",
  last_seen_at: "2026-10-12T00:01:00Z", delivery_state: "PENDING", simulated: true,
});

describe("Survivor Location Accuracy simulation evaluator", () => {
  afterEach(cleanup);

  it("calculates horizontal error and renders exactly one entry per truth actor", () => {
    const one = target("target-1", 12.972004, 77.592);
    const two = target("target-2", 12.973, 77.593005);
    render(<SurvivorLocationAccuracy runId="run-a" truth={truth} targets={[one, two]} simulationOnly />);
    expect(screen.getAllByTestId("survivor-accuracy-entry")).toHaveLength(2);
    expect(screen.getByText("target-1")).toBeInTheDocument();
    expect(screen.getByText("target-2")).toBeInTheDocument();
    expect(horizontalDistanceM(
      { latitude: 12.972, longitude: 77.592 },
      { latitude: one.latitude, longitude: one.longitude },
    )).toBeCloseTo(.445, 2);
    expect(screen.getAllByText("MEASURED")).toHaveLength(2);
    expect(screen.getByText(/Valid matches/)).toHaveTextContent("Valid matches 2 · unavailable 0");
  });

  it("does not present unavailable or unmatched locations as measurements", () => {
    render(<SurvivorLocationAccuracy runId="run-a" truth={truth} targets={[]} simulationOnly />);
    expect(screen.getAllByText("WAITING FOR DETECTION")).toHaveLength(2);
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.queryByText(/mean/)).not.toBeInTheDocument();

    cleanup();
    const distant = target("elsewhere", 13.1, 77.7);
    render(<SurvivorLocationAccuracy runId="run-a" truth={truth} targets={[distant]} simulationOnly />);
    expect(screen.getAllByText("UNMATCHED")).toHaveLength(2);
    expect(screen.queryByText("target-1")).not.toBeInTheDocument();
  });

  it("retains stable one-to-one associations across reacquisition", () => {
    const initial = [target("target-1", 12.972004, 77.592), target("target-2", 12.973004, 77.593)];
    const first = matchSurvivorLocations(truth, initial);
    const prior = new Map(first.map((item) => [item.survivorId, item.target!.target_id]));
    const reacquired = [target("target-1", 12.972006, 77.592001), target("target-2", 12.973002, 77.593003)];
    const second = matchSurvivorLocations(truth, reacquired, prior);
    expect(second.map((item) => item.target?.target_id)).toEqual(["target-1", "target-2"]);
    expect(new Set(second.map((item) => item.target?.target_id)).size).toBe(2);
  });

  it("is absent outside simulation operation", () => {
    render(<SurvivorLocationAccuracy runId="run-a" truth={truth} targets={[]} simulationOnly={false} />);
    expect(screen.queryByLabelText("Survivor Location Accuracy — Simulation Evaluation")).not.toBeInTheDocument();
  });
});
