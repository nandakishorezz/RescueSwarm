import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MultiDronePage, normalizeFleetStatus } from "./MultiDronePage";
import { api } from "../../services/api";

describe("MultiDronePage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("normalizeFleetStatus safely supplies default arrays when properties are omitted", () => {
    const emptyStatus = normalizeFleetStatus({ state: "IDLE" });
    expect(emptyStatus.targets).toEqual([]);
    expect(emptyStatus.assignments).toEqual([]);
    expect(emptyStatus.deliveries).toEqual([]);
    expect(emptyStatus.events).toEqual([]);
    expect(emptyStatus.vehicles).toEqual([]);
    expect(emptyStatus.trails).toEqual({});
    expect(emptyStatus.error).toBeNull();
  });

  it("renders without crashing when multiDroneStatus returns incomplete schema", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "IDLE",
      session: null,
      vehicles: [],
      targets: [],
      events: [],
    } as any);

    render(<MultiDronePage />);

    expect(screen.getByRole("heading", { name: "Multi-Drone Simulation" })).toBeInTheDocument();
    expect(screen.getByText("Confirmed canonical survivors: 0")).toBeInTheDocument();
    expect(screen.getByText("Service assignments: 0")).toBeInTheDocument();
    expect(screen.getByText("Verified deliveries: 0")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Session none")).toBeInTheDocument();
    });
  });

  it("shows authoritative priority, source, queue order and waiting reason", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "RUNNING",
      session: { session_id: "s2c", mission_id: "m1", run_id: "r1", configuration: {} },
      vehicles: [],
      targets: [{ canonical_id: "survivor-001", session_id: "s2c", confirmation_status: "CONFIRMED",
        observation_count: 3, source_drone_ids: ["drone_01"], priority: "CRITICAL", priority_source: "OPERATOR" }],
      assignments: [{ assignment_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", session_id: "s2c",
        canonical_survivor_id: "survivor-001", state: "QUEUED", priority: "CRITICAL",
        priority_source: "OPERATOR", queue_position: 1, reason: "No eligible SERVICER" }],
      deliveries: [], events: [], error: null,
    } as any);
    render(<MultiDronePage />);
    await waitFor(() => {
      expect(screen.getByLabelText("Priority for survivor-001")).toHaveValue("CRITICAL");
      expect(screen.getByText("OPERATOR")).toBeInTheDocument();
      expect(screen.getByText("#1 · CRITICAL")).toBeInTheDocument();
      expect(screen.getByText("No eligible SERVICER")).toBeInTheDocument();
    });
  });

  it("handles empty or disconnected fleet vehicles safely without throwing", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "WAITING_FOR_TELEMETRY",
      state_reason: "Waiting for vehicle connections",
      session: { session_id: "test-session-12345", mission_id: "m1", run_id: "r1", configuration: {} },
      vehicles: [
        {
          vehicle_id: "drone_01",
          role: "SEARCHER",
          system_id: 1,
          connection: "tcp:127.0.0.1:5760",
          state: "WAITING_FOR_HEARTBEAT",
          connected: false,
          telemetry_age_s: null,
          telemetry: null,
          progress_percent: 0,
          camera_status: "NO_FRAMES",
          camera_frame_count: 0,
          camera_decoded_count: 0,
          camera_inference_count: 0,
          camera_age_s: null,
          last_event: null,
        },
      ],
      targets: [],
      assignments: [],
      deliveries: [],
      events: [],
      error: null,
    } as any);

    render(<MultiDronePage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "drone_01" })).toBeInTheDocument();
      expect(screen.getByText("WAITING FOR HEARTBEAT")).toBeInTheDocument();
      expect(screen.getByText("Link STALE")).toBeInTheDocument();
      expect(screen.getByText("Altitude —")).toBeInTheDocument();
      expect(screen.getByText("Speed —")).toBeInTheDocument();
      expect(screen.getByText("GPS —")).toBeInTheDocument();
      expect(screen.getByText("Search 0.0%")).toBeInTheDocument();
    });
  });

  it("renders authoritative fleet state header and disables controls when RUNNING", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "RUNNING",
      state_reason: "Fleet active",
      session: { session_id: "active-session-abcdef", mission_id: "m1", run_id: "r1", configuration: {} },
      vehicles: [
        {
          vehicle_id: "drone_01",
          role: "SEARCHER",
          system_id: 1,
          connection: "tcp:127.0.0.1:5760",
          state: "SEARCHING",
          connected: true,
          telemetry_age_s: 0.1,
          telemetry: {
            latitude: 12.97,
            longitude: 77.59,
            relative_altitude_m: 15.0,
            ground_speed_m_s: 4.5,
            flight_mode: "AUTO",
            armed: true,
            gps_fix_type: 3,
          },
          progress_percent: 25.0,
          camera_status: "LIVE",
          camera_frame_count: 120,
          camera_decoded_count: 120,
          camera_inference_count: 119,
          camera_age_s: 0.05,
          last_event: "SEARCH_PROGRESS",
        },
      ],
      failed_vehicles: [],
      targets: [],
      assignments: [],
      deliveries: [],
      events: [
        {
          timestamp: "2026-10-15T00:00:01Z",
          vehicle_id: null,
          type: "FLEET_START_REQUESTED",
          message: "Fleet startup requested",
          severity: "INFO",
        },
        {
          timestamp: "2026-10-15T00:00:10Z",
          vehicle_id: "drone_01",
          type: "SEARCH_STARTED",
          message: "drone_01 search activity started",
          severity: "INFO",
        },
      ],
      error: null,
    } as any);

    render(<MultiDronePage />);

    await waitFor(() => {
      // Header state badge
      expect(screen.getByText("RUNNING")).toBeInTheDocument();
      expect(screen.getByText("Fleet active")).toBeInTheDocument();

      // Start fleet button should be disabled when running
      expect(screen.getByRole("button", { name: "Start fleet" })).toBeDisabled();

      // Safe fleet return / stop button should be enabled when running
      expect(screen.getByRole("button", { name: "Safe fleet return / stop" })).toBeEnabled();

      // Spacing input and roster controls should be disabled
      expect(screen.getByLabelText("Spawn spacing")).toBeDisabled();
      expect(screen.getByRole("button", { name: "Add drone" })).toBeDisabled();

      // Vehicle state badge
      expect(screen.getByText("SEARCHING")).toBeInTheDocument();
      expect(screen.getByText("Link CONNECTED")).toBeInTheDocument();
      expect(screen.getByText("Mode AUTO")).toBeInTheDocument();
      expect(screen.getByText("ARMED")).toBeInTheDocument();
      expect(screen.getByText("Altitude 15.0 m")).toBeInTheDocument();
      expect(screen.getByText("Speed 4.5 m/s")).toBeInTheDocument();
      expect(screen.getByText("Search 25.0%")).toBeInTheDocument();

      // Role restriction
      expect(screen.getByText("Delivery forbidden by backend role gate.")).toBeInTheDocument();

      // Timeline events
      expect(screen.getByText("State Transition Timeline")).toBeInTheDocument();
      expect(screen.getByText("Fleet startup requested")).toBeInTheDocument();
      expect(screen.getByText("drone_01 search activity started")).toBeInTheDocument();
    });
  });

  it("displays degraded indicator when a vehicle subsystem has an alert", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "DEGRADED",
      state_reason: "Telemetry stale for drone_02",
      session: { session_id: "active-session-123456", mission_id: "m1", run_id: "r1", configuration: {} },
      vehicles: [
        {
          vehicle_id: "drone_01",
          role: "SEARCHER",
          system_id: 1,
          state: "SEARCHING",
          connected: true,
          telemetry: null,
          progress_percent: 40.0,
          camera_status: "LIVE",
          camera_frame_count: 50,
          camera_decoded_count: 50,
          camera_inference_count: 50,
          camera_age_s: 0.1,
          last_event: null,
        },
        {
          vehicle_id: "drone_02",
          role: "SERVICER",
          system_id: 2,
          state: "FAILED",
          error: "SITL connection lost",
          connected: false,
          telemetry: null,
          progress_percent: 0,
          camera_status: "NO_FRAMES",
          camera_frame_count: 0,
          camera_decoded_count: 0,
          camera_inference_count: 0,
          camera_age_s: null,
          last_event: "LINK_LOST",
        },
      ],
      failed_vehicles: ["drone_02"],
      targets: [],
      assignments: [],
      deliveries: [],
      events: [],
      error: null,
    } as any);

    render(<MultiDronePage />);

    await waitFor(() => {
      expect(screen.getByText("DEGRADED")).toBeInTheDocument();
      expect(screen.getByText("Telemetry stale for drone_02")).toBeInTheDocument();
      expect(screen.getByText("⚠️ SITL connection lost")).toBeInTheDocument();
    });
  });

  it("renders Phase 2A Shared Survivor Registry and Service Queue panels with live data", async () => {
    vi.spyOn(api, "multiDroneStatus").mockResolvedValue({
      state: "RUNNING",
      state_reason: "Fleet active",
      session: { session_id: "active-session-123456", mission_id: "m1", run_id: "r1", configuration: {} },
      vehicles: [],
      targets: [
        {
          canonical_id: "survivor-001",
          session_id: "active-session-123456",
          confirmation_status: "CONFIRMED",
          observation_count: 4,
          source_drone_ids: ["drone_01", "drone_02"],
          first_seen_at: "2026-10-15T10:00:00Z",
          last_seen_at: "2026-10-15T10:00:05Z",
          latitude: 37.7749,
          longitude: -122.4194,
          uncertainty_m: 1.2,
          confidence: 0.92,
          assigned_servicer_id: "drone_02",
          assignment_state: "ASSIGNED",
        },
      ],
      assignments: [
        {
          assignment_id: "11111111-2222-3333-4444-555555555555",
          session_id: "active-session-123456",
          canonical_survivor_id: "survivor-001",
          servicer_id: "drone_02",
          state: "ASSIGNED",
          reason: "Exclusively assigned to eligible servicer drone_02",
          created_at: "2026-10-15T10:00:05Z",
          updated_at: "2026-10-15T10:00:05Z",
        },
        {
          assignment_id: "66666666-7777-8888-9999-000000000000",
          session_id: "active-session-123456",
          canonical_survivor_id: "survivor-002",
          servicer_id: null,
          state: "QUEUED",
          reason: "No eligible SERVICER available; drone_02 is busy",
          created_at: "2026-10-15T10:00:10Z",
          updated_at: "2026-10-15T10:00:10Z",
        },
      ],
      deliveries: [],
      events: [],
      error: null,
    } as any);

    render(<MultiDronePage />);

    await waitFor(() => {
      // Panel headings
      expect(screen.getByRole("heading", { name: "Shared Survivor Registry" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Service Queue & Assignments" })).toBeInTheDocument();

      // Registry table items (survivor-001 appears in both registry and queue)
      expect(screen.getAllByText("survivor-001").length).toBe(2);
      expect(screen.getAllByText("drone_01").length).toBeGreaterThan(0);
      expect(screen.getAllByText("drone_02").length).toBeGreaterThan(0);
      expect(screen.getByText("ASSIGNED (drone_02)")).toBeInTheDocument();

      // Service Queue table items
      expect(screen.getByText("11111111")).toBeInTheDocument();
      expect(screen.getByText("66666666")).toBeInTheDocument();
      expect(screen.getByText("No eligible SERVICER available; drone_02 is busy")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "ACK" })).toBeInTheDocument();
    });
  });
});
