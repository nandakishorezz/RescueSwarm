import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import { useGcsStore } from "../stores/gcsStore";
import type { Target } from "../types/domain";
import { GroundTruthVsPredictedPanel } from "./GroundTruthVsPredictedPanel";
import { MapView } from "./MapView";
import { TargetList } from "./TargetList";

const survivor = (count = 1): Target => ({
  target_id: "survivor-0001", mission_id: "mission-a", state: "CONFIRMED",
  latitude: 12.9717, longitude: 77.5947, east_m: 11, north_m: 9,
  detection_confidence: .9, geolocation_uncertainty_m: 2.4, observation_count: count,
  first_seen_at: "2026-10-09T10:00:00Z", last_seen_at: "2026-10-09T10:01:00Z",
  delivery_state: "PENDING", simulated: true, geolocation_quality: "STANDARD_GNSS_LIMITED",
});

describe("persistent survivor records", () => {
  beforeEach(() => {
    localStorage.removeItem("rescueswarm.simulationScenario");
    useGcsStore.setState({ mission: null, targets: {}, vehicles: {}, trails: {}, events: [], error: null });
  });
  afterEach(cleanup);

  it("updates one survivor record and one map marker for repeated observations", () => {
    useGcsStore.setState({ targets: { [survivor().target_id]: survivor() } });
    render(<><MapView /><TargetList /></>);
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(1);
    expect(screen.getByText("ID survivor")).toBeInTheDocument();
    expect(screen.getByText(/Location: APPROXIMATE/)).toBeInTheDocument();

    act(() => useGcsStore.setState({ targets: { [survivor().target_id]: survivor(2) } }));
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(1);
    expect(screen.getByText(/2 observations/)).toBeInTheDocument();
  });

  it("clears session-scoped survivor records when a new mission begins", () => {
    useGcsStore.setState({ mission: { id: "mission-a", name: "A", state: "RUNNING", created_at: "2026-10-09T10:00:00Z", elapsed_seconds: 0, coverage_percent: 0 }, targets: { [survivor().target_id]: survivor() } });
    render(<MapView />);
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(1);
    act(() => useGcsStore.getState().setMission({ id: "mission-b", name: "B", state: "DRAFT", created_at: "2026-10-09T10:01:00Z", elapsed_seconds: 0, coverage_percent: 0 }));
    expect(screen.queryAllByTestId("autonomous-survivor-marker")).toHaveLength(0);
  });

  it("keeps the survivor marker and panel cardinality equal to canonical IDs", () => {
    const second = { ...survivor(), target_id: "survivor-0002", east_m: 17, north_m: 9, latitude: 12.9718 };
    useGcsStore.setState({ targets: {
      [survivor().target_id]: survivor(), [second.target_id]: second,
    } });
    render(<><MapView /><TargetList /></>);
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(2);
    expect(screen.getAllByText("ID survivor", { selector: "small" })).toHaveLength(2);

    // A retransmitted update must replace the first record, never append a
    // second marker/card for the same stable target ID.
    act(() => useGcsStore.setState({ targets: {
      [survivor().target_id]: survivor(4), [second.target_id]: second,
    } }));
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(2);
    expect(screen.getAllByText("ID survivor", { selector: "small" })).toHaveLength(2);
    expect(screen.getByText(/4 observations/)).toBeInTheDocument();
  });

  it("does not count ground-truth rows as autonomous detections and filters out superseded targets", () => {
    const canonical = survivor();
    const superseded: Target = {
      ...survivor(),
      target_id: "survivor-0002-dup",
      state: "REJECTED",
    };
    useGcsStore.setState({
      targets: {
        [canonical.target_id]: canonical,
        [superseded.target_id]: superseded,
      },
    });

    render(
      <>
        <MapView />
        <TargetList />
        <GroundTruthVsPredictedPanel
          scenarioSurvivors={[
            { id: "truth-1", latitude: 12.9717, longitude: 77.5947 },
            { id: "truth-2", latitude: 12.9730, longitude: 77.5960 },
          ]}
          targets={useGcsStore.getState().targets}
        />
      </>
    );

    // Markers must only be for active canonical targets (1 marker)
    expect(screen.getAllByTestId("autonomous-survivor-marker")).toHaveLength(1);

    // Target list must only show 1 card, not the superseded one
    expect(screen.getAllByText("ID survivor", { selector: "small" })).toHaveLength(1);
    expect(screen.getByText("SURVIVORS").nextSibling?.textContent).toBe("1");

    // In GroundTruthVsPredictedPanel: truth-1 matches canonical, truth-2 is GROUND TRUTH ONLY
    expect(screen.getByText("truth-1")).toBeInTheDocument();
    expect(screen.getByText("truth-2")).toBeInTheDocument();
  });

  it("does not render a candidate as a 2D autonomous marker", () => {
    useGcsStore.setState({ targets: { candidate: { ...survivor(), target_id: "candidate", state: "CANDIDATE" } } });
    render(<><MapView /><TargetList /></>);
    expect(screen.queryAllByTestId("autonomous-survivor-marker")).toHaveLength(0);
    expect(screen.getByText("SURVIVORS").nextSibling?.textContent).toBe("0");
  });

  it("ignores delayed old-session targets and cannot resurrect a rejected alias", () => {
    useGcsStore.getState().setMission({
      id: "mission-a", name: "A", state: "RUNNING", created_at: "2026-10-09T10:00:00Z",
      elapsed_seconds: 0, coverage_percent: 0,
    });
    const deliver = (missionId: string, target: Target, type: "target.confirmed" | "target.rejected") =>
      useGcsStore.getState().applyEnvelope({
        type, mission_id: missionId, vehicle_id: "drone_01", sequence: 1,
        timestamp: new Date().toISOString(), payload: target as unknown as Record<string, unknown>,
      });
    deliver("mission-a", survivor(), "target.confirmed");
    deliver("mission-a", { ...survivor(), state: "REJECTED" }, "target.rejected");
    deliver("mission-a", survivor(), "target.confirmed");
    expect(useGcsStore.getState().targets[survivor().target_id].state).toBe("REJECTED");

    useGcsStore.getState().setMission({
      id: "mission-b", name: "B", state: "RUNNING", created_at: "2026-10-09T10:01:00Z",
      elapsed_seconds: 0, coverage_percent: 0,
    });
    deliver("mission-a", survivor(), "target.confirmed");
    expect(Object.keys(useGcsStore.getState().targets)).toHaveLength(0);
  });
});

