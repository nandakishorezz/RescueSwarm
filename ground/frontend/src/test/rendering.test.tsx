import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { MissionSummary } from "../components/MissionSummary";
import { TargetList } from "../components/TargetList";
import { VehiclePanel } from "../components/VehiclePanel";
import { useGcsStore } from "../stores/gcsStore";

beforeEach(() => useGcsStore.setState({ mission: null, targets: {} }));
afterEach(cleanup);

it("renders target and delivery state", () => {
  useGcsStore.setState({ targets: {
    target_1: {
      target_id: "target_1", mission_id: "mission", state: "DELIVERED", latitude: 12,
      longitude: 77, east_m: 2, north_m: 3, detection_confidence: .94,
      geolocation_uncertainty_m: 3.2, observation_count: 4,
      first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString(),
      delivery_state: "DELIVERED", simulated: true,
    },
  } });
  render(<TargetList />);
  expect(screen.getByText("DELIVERED", { selector: "b" })).toBeInTheDocument();
  expect(screen.getByText("Delivery: DELIVERED")).toBeInTheDocument();
});

it("separates target detection confidence from geographic uncertainty", () => {
  useGcsStore.setState({ targets: {
    target_1: {
      target_id: "target_1", mission_id: "mission", state: "CONFIRMED", latitude: 17.45,
      longitude: 78.38, east_m: 3, north_m: 4, detection_confidence: .88,
      geolocation_uncertainty_m: 2.6, observation_count: 7,
      first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString(),
      delivery_state: "PENDING", simulated: false, geolocation_quality: "STANDARD_GNSS_LIMITED",
    },
  } });
  render(<TargetList />);
  expect(screen.getByText("Detection confidence 88%")).toBeInTheDocument();
  expect(screen.getByText(/Position uncertainty ±2.6 m/)).toBeInTheDocument();
  expect(screen.getByText("Quality: STANDARD_GNSS_LIMITED")).toBeInTheDocument();
});

it("renders mission progress and state", () => {
  useGcsStore.setState({ mission: {
    id: "mission", name: "Search Test", state: "RUNNING", created_at: new Date().toISOString(),
    elapsed_seconds: 20, coverage_percent: 62,
  } });
  render(<MissionSummary />);
  expect(screen.getByText("62%")).toBeInTheDocument();
  expect(screen.getByText("RUNNING")).toBeInTheDocument();
  expect(screen.getByText("Search Test")).toBeInTheDocument();
});

it("renders the competition maximum of ten canonical targets", () => {
  const now = new Date().toISOString();
  useGcsStore.setState({ targets: Object.fromEntries(Array.from({ length: 10 }, (_, index) => {
    const id = `target_${index + 1}`;
    return [id, {
      target_id: id, mission_id: "mission", state: "CONFIRMED", latitude: 17.45,
      longitude: 78.38 + index / 10000, east_m: index * 10, north_m: 5,
      detection_confidence: .85, geolocation_uncertainty_m: 3,
      observation_count: 4, first_seen_at: now, last_seen_at: now,
      delivery_state: "PENDING", simulated: true,
    }];
  })) });
  const { container } = render(<TargetList />);
  expect(container.querySelectorAll("article.target")).toHaveLength(10);
  expect(screen.getByText("#10")).toBeInTheDocument();
});

it("labels live normalized telemetry as ArduPilot SITL", () => {
  useGcsStore.setState({
    executionSource: "SITL",
    vehicles: {
      drone_01: {
        vehicle_id: "drone_01", name: "ArduCopter SITL", connected: true,
        latitude: -35.36, longitude: 149.16, relative_altitude_m: 15,
        roll_deg: 0, pitch_deg: 0, yaw_deg: 90, ground_speed_m_s: 4,
        battery_percent: 88, battery_voltage: 15.8, gps_fix_type: 6,
        satellites: 10, flight_mode: "AUTO", armed: true, link_quality: 100,
        current_task: "SEARCH", last_update: new Date().toISOString(), sequence: 4,
        simulated: false, current_mission_item: 5, total_mission_items: 10,
      },
    },
  });
  render(<VehiclePanel />);
  expect(screen.getByText("ARDUPILOT SITL")).toBeInTheDocument();
  expect(screen.getByText("5 / 10")).toBeInTheDocument();
});
