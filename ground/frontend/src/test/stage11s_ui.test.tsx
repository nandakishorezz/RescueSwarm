import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import fixture from "../../../../data/fixtures/stage11s_spatial_geometry.json";
import { HealthPanel } from "../components/HealthPanel";
import { ReplayPlayer } from "../components/ReplayPlayer";
import { SpatialInspector } from "../components/SpatialInspector";
import { StatusBar } from "../components/StatusBar";
import { TargetList } from "../components/TargetList";
import { VehiclePanel } from "../components/VehiclePanel";
import { ViewControls } from "../components/ViewControls";
import { useGcsStore } from "../stores/gcsStore";
import type { DigitalTwinSnapshot, Target, VehicleState } from "../types/domain";

const twin: DigitalTwinSnapshot = {
  mission_id: "mission", generated_at: "2026-01-01T00:00:00Z", execution_source: "SITL", sequence: 1,
  origin: { latitude: 12, longitude: 77, altitude_m: 0 },
  ground: { provider: "FLAT", elevation_up_m: 0, evidence_class: "CONFIGURED_ASSUMPTION" },
  vehicle_geometry: {
    profile_id: "SITL_REFERENCE_PROFILE", length_m: .6, width_m: .6, height_m: .2, arm_span_m: .5, rotor_radius_m: .1,
    body_origin_offset_m: [0, 0, 0], autopilot_reference_offset_m: [0, 0, 0], camera_mount_position_m: [0, 0, 0], payload_mount_position_m: [0, 0, .1], evidence_class: "CONFIGURED_ASSUMPTION",
  },
  camera: { calibration: fixture.calibration as DigitalTwinSnapshot["camera"]["calibration"], extrinsics: fixture.extrinsics as DigitalTwinSnapshot["camera"]["extrinsics"] },
  vehicles: [{ vehicle_id: "drone_01", position_enu_m: [10, 20, 15], position_wgs84: { latitude: 12, longitude: 77, altitude_m: 15 }, quaternion_body_to_enu: fixture.quaternion_body_to_enu as [number, number, number, number], velocity_enu_m_s: [0, 4, 0], telemetry_timestamp: "2026-01-01T00:00:00Z", evidence_class: "SITL", stale: false }],
  observations: [], launch_landing_zone: { width_m: 3.6576, length_m: 3.6576, physical_fit_verified: false }, hardware_gate_h1: "PENDING",
};

beforeEach(() => useGcsStore.setState({
  digitalTwin: twin, selectedVehicleId: "drone_01", selectedTargetId: null, selectedObservationId: null,
  viewMode: "2D", scaleMode: "ICON", replay: null, vehicles: {}, events: [], mission: null,
  executionSource: "SITL", hardwareGateH1: { gate_id: "H1", name: "Physical prototype", state: "PENDING", reason: "Unavailable" },
}));
afterEach(cleanup);

describe("Stage 11S operator interface", () => {
  it("keeps simulation and H1 truth visible", () => {
    render(<StatusBar />);
    expect(screen.getByText("SIMULATION")).toBeInTheDocument();
    expect(screen.getByText("H1 PENDING")).toBeInTheDocument();
  });

  it("synchronizes 2D/3D/split and physical/icon scale state", () => {
    render(<ViewControls />);
    fireEvent.click(screen.getByText("SPLIT")); fireEvent.click(screen.getByText("TRUE SCALE"));
    expect(useGcsStore.getState().viewMode).toBe("SPLIT");
    expect(useGcsStore.getState().scaleMode).toBe("TRUE_SCALE");
  });

  it("shows coordinate evidence and a mathematically valid principal-point ray", () => {
    render(<SpatialInspector />);
    expect(screen.getByText("SPATIAL INSPECTOR")).toBeInTheDocument();
    expect(screen.getByText(/0.00000, 0.00000, -1.00000/)).toBeInTheDocument();
    expect(screen.getByText(/10.000, 20.000, 0.000/)).toBeInTheDocument();
    expect(screen.getByText(/FLAT · CONFIGURED_ASSUMPTION/)).toBeInTheDocument();
  });

  it("uses explicit no-data and stale-safe states", () => {
    useGcsStore.setState({ digitalTwin: { ...twin, vehicles: [{ ...twin.vehicles[0], stale: true }] } });
    const { rerender } = render(<HealthPanel />);
    expect(screen.getByText("STALE")).toBeInTheDocument();
    rerender(<ReplayPlayer />);
    expect(screen.getByText("NO TIMESTAMPED TELEMETRY AVAILABLE FOR REPLAY")).toBeInTheDocument();
  });

  it("renders the competition-scale operator state for two vehicles and ten targets", () => {
    const vehicle: VehicleState = {
      vehicle_id: "drone_01", name: "Drone 01", connected: true, armed: true, flight_mode: "AUTO",
      latitude: 12, longitude: 77, relative_altitude_m: 15, ground_speed_m_s: 4,
      roll_deg: 0, pitch_deg: 0, yaw_deg: 0, battery_voltage: 15.5, battery_percent: 90,
      gps_fix_type: 3, satellites: 14, link_quality: 100, simulated: true,
      current_task: "SEARCHING", last_update: "2026-01-01T00:00:00Z", sequence: 1,
    };
    const targets: Record<string, Target> = Object.fromEntries(Array.from({ length: 10 }, (_, index) => {
      const id = `target_${index + 1}`;
      return [id, {
        target_id: id, mission_id: "mission", state: "CONFIRMED", delivery_state: "PENDING",
        latitude: 12 + index / 10000, longitude: 77, east_m: index, north_m: index,
        detection_confidence: .9, geolocation_uncertainty_m: 2, observation_count: 3,
        first_seen_at: "2026-01-01T00:00:00Z", last_seen_at: "2026-01-01T00:00:01Z", simulated: true,
      } satisfies Target];
    }));
    useGcsStore.setState({ vehicles: { drone_01: vehicle, drone_02: { ...vehicle, vehicle_id: "drone_02", name: "Drone 02" } }, targets });
    render(<><VehiclePanel /><TargetList /></>);
    expect(screen.getByText("DRONE 01")).toBeInTheDocument();
    expect(screen.getByText("DRONE 02")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(12);
  });
});
