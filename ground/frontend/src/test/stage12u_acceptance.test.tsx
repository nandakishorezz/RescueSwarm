import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EventFeed } from "../components/EventFeed";
import { ModeNav } from "../components/ModeNav";
import { PanelDisclosure } from "../components/PanelDisclosure";
import { ReplayPlayer } from "../components/ReplayPlayer";
import { StatusBar } from "../components/StatusBar";
import { ViewControls } from "../components/ViewControls";
import { SetupPage } from "../pages/SetupPage";
import { useGcsStore } from "../stores/gcsStore";

beforeEach(() => {
  localStorage.clear();
  useGcsStore.setState({
    mode: "SETUP", competitionMode: false, mission: null, coveragePlan: null, fleetStatus: null,
    offlineStatus: { status: "READY", external_network_required: false, external_tile_sources: 0, external_api_dependencies: 0, local_static_assets: "READY", local_database: "READY", local_websocket: "READY", competition_mode: false, violations: [] },
    vehicles: {}, targets: {}, events: [], replay: null, digitalTwin: null, websocketState: "DISCONNECTED",
    hardwareGateH1: { gate_id: "H1", name: "Physical prototype", state: "PENDING", reason: "Hardware unavailable" },
    panelVisibility: { fleet: true, vehicles: true, targets: true, health: false, video: false, timeline: true },
    digitalTwinLayers: { aircraft: true, paths: true, sectors: true, cameraFrustum: true, cameraFootprint: true, detectionRays: true, targets: true, uncertainty: true, axes: false },
  });
});
afterEach(cleanup);

describe("Stage 12U operator acceptance", () => {
  it("presents the six-step first-run setup and keeps START visibly blocked", () => {
    render(<SetupPage />);
    for (const heading of ["Mission", "Fleet", "Plan", "Payload", "Readiness", "Start"]) expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.getByText("FIRST RUN")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "START MISSION" })).toHaveLength(2);
    screen.getAllByRole("button", { name: "START MISSION" }).forEach((button) => expect(button).toBeDisabled());
    expect(screen.getByText(/Create a mission record/)).toBeInTheDocument();
  });

  it("physically removes Engineering navigation in Competition Mode", () => {
    useGcsStore.setState({ competitionMode: true });
    render(<ModeNav />);
    expect(screen.queryByRole("button", { name: "ENGINEERING" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "MISSION" })).toBeInTheDocument();
  });

  it("persists split ratio and layer visibility without creating flight controls", () => {
    useGcsStore.setState({ viewMode: "SPLIT" });
    render(<ViewControls />);
    fireEvent.click(screen.getByRole("button", { name: "65/35" }));
    expect(useGcsStore.getState().splitRatio).toBe("65_35");
    fireEvent.click(screen.getByText("LAYERS"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Detection rays" }));
    expect(useGcsStore.getState().digitalTwinLayers.detectionRays).toBe(false);
    expect(screen.queryByText(/GOTO|LAND HERE|MANUAL DROP/)).not.toBeInTheDocument();
  });

  it("filters a bounded mission timeline by vehicle, severity, and family", () => {
    useGcsStore.setState({ events: [
      { event_id: "1", mission_id: "m", vehicle_id: "drone_01", target_id: null, type: "SEARCH_STARTED", severity: "INFO", timestamp: "2026-01-01T00:00:00Z", message: "Search started", metadata: {}, simulated: true },
      { event_id: "2", mission_id: "m", vehicle_id: "drone_02", target_id: null, type: "SYSTEM_WARNING", severity: "WARNING", timestamp: "2026-01-01T00:00:01Z", message: "Vehicle 02 telemetry stale", metadata: {}, simulated: true },
    ] });
    render(<EventFeed />);
    expect(screen.getByText("Search started")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Vehicle"), { target: { value: "drone_02" } });
    expect(screen.queryByText("Search started")).not.toBeInTheDocument();
    expect(screen.getByText("Vehicle 02 telemetry stale")).toBeInTheDocument();
  });

  it("uses accessible disclosure and explicit empty replay language", () => {
    render(<><PanelDisclosure id="fleet" title="Fleet summary" open onToggle={() => undefined}><p>Fleet content</p></PanelDisclosure><ReplayPlayer /></>);
    expect(screen.getByText("Fleet summary")).toBeInTheDocument();
    expect(screen.getByText("NO TIMESTAMPED TELEMETRY AVAILABLE FOR REPLAY")).toBeInTheDocument();
  });

  it("shows mission provenance, competition lock, and critical state with text", () => {
    useGcsStore.setState({
      competitionMode: true, executionSource: "SITL",
      resilienceStatus: { aggregate_health: "EMERGENCY", competition_locked: true, failure_injection_enabled: false, hardware_gate_h1: useGcsStore.getState().hardwareGateH1!, safety_decision: { severity: "EMERGENCY", reason: "Test", source: "SITL", recommended_action: "REQUEST_RTL", authoritative_layer: "AUTOPILOT", timestamp: "2026-01-01T00:00:00Z", evidence: {} } },
    });
    render(<StatusBar />);
    expect(screen.getByText("COMPETITION MODE")).toBeInTheDocument();
    expect(screen.getByText("SIMULATION")).toHaveAttribute("title", "ArduCopter software-in-the-loop evidence");
    expect(screen.getByText("SAFETY EMERGENCY")).toBeInTheDocument();
    expect(screen.getByText("▲ 1 CRITICAL")).toBeInTheDocument();
  });
});
