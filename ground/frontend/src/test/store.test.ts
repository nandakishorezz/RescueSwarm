import { beforeEach, describe, expect, it } from "vitest";

import { classifyTelemetryAge, useGcsStore } from "../stores/gcsStore";
import type { RealtimeEnvelope, VehicleState } from "../types/domain";

const vehicle: VehicleState = {
  vehicle_id: "drone_01", name: "Simulator", connected: true, latitude: 12, longitude: 77,
  relative_altitude_m: 20, roll_deg: 0, pitch_deg: 0, yaw_deg: 90, ground_speed_m_s: 7,
  battery_percent: 80, battery_voltage: 15, gps_fix_type: 3, satellites: 14,
  flight_mode: "SIM_SEARCH", armed: true, link_quality: 95, current_task: "SEARCH",
  last_update: new Date().toISOString(), sequence: 1, simulated: true,
};

function envelope(sequence: number): RealtimeEnvelope {
  return {
    type: "vehicle.telemetry", timestamp: new Date().toISOString(), mission_id: "mission",
    vehicle_id: "drone_01", sequence, payload: { ...vehicle, sequence, longitude: 77 + sequence / 1000 },
  };
}

describe("GCS store", () => {
  beforeEach(() => useGcsStore.setState({ mission: null, vehicles: {}, trails: {}, targets: {}, events: [], cameraFrame: null, competitionMode: false, mode: "SETUP" }));

  it("updates normalized vehicle state and rejects stale sequence", () => {
    useGcsStore.getState().applyEnvelope(envelope(2));
    useGcsStore.getState().applyEnvelope(envelope(1));
    expect(useGcsStore.getState().vehicles.drone_01.sequence).toBe(2);
    expect(useGcsStore.getState().trails.drone_01).toHaveLength(1);
  });

  it("classifies telemetry age", () => {
    expect(classifyTelemetryAge(1.9)).toBe("CONNECTED");
    expect(classifyTelemetryAge(2)).toBe("DEGRADED");
    expect(classifyTelemetryAge(5.1)).toBe("STALE");
  });

  it("prevents engineering mode in competition configuration", () => {
    useGcsStore.getState().setMode("ENGINEERING");
    useGcsStore.getState().setCompetitionMode(true);
    expect(useGcsStore.getState().mode).toBe("SETUP");
    useGcsStore.getState().setMode("ENGINEERING");
    expect(useGcsStore.getState().mode).toBe("SETUP");
  });

  it("labels real MAVLink envelopes as SITL", () => {
    const message = envelope(3);
    message.payload = { ...message.payload, source: "SITL", simulated: false };
    useGcsStore.getState().applyEnvelope(message);
    expect(useGcsStore.getState().executionSource).toBe("SITL");
    expect(useGcsStore.getState().vehicles.drone_01.simulated).toBe(false);
  });

  it("tracks evidence-backed rescue state and payload inventory", () => {
    useGcsStore.setState({
      mission: {
        id: "mission", name: "Rescue", state: "RUNNING", created_at: new Date().toISOString(),
        elapsed_seconds: 1, coverage_percent: 10,
      },
    });
    useGcsStore.getState().applyEnvelope({
      type: "mission.progress", timestamp: new Date().toISOString(), mission_id: "mission",
      vehicle_id: "drone_01", sequence: 10,
      payload: { rescue_state: "GUIDED_APPROACH", payload_remaining: 1, execution_source: "SITL" },
    });
    expect(useGcsStore.getState().rescueState).toBe("GUIDED_APPROACH");
    expect(useGcsStore.getState().payloadRemaining).toBe(1);
  });

  it("keeps Hardware Gate H1 explicitly pending", () => {
    useGcsStore.getState().setHardwareGateH1({
      gate_id: "H1_PHYSICAL_PROTOTYPE", name: "Pi5 + OV9281 + Pixhawk/M8N",
      state: "PENDING", reason: "Hardware unavailable",
    });
    expect(useGcsStore.getState().hardwareGateH1?.state).toBe("PENDING");
  });

  it("rehydrates authoritative state after WebSocket reconnect", () => {
    useGcsStore.getState().applyEnvelope({
      type: "system.snapshot", timestamp: new Date().toISOString(), mission_id: "mission",
      vehicle_id: null, sequence: 0,
      payload: {
        mission: { id: "mission", name: "Recovered", state: "RUNNING", created_at: new Date().toISOString(), elapsed_seconds: 25, coverage_percent: 40 },
        vehicles: [{ ...vehicle, sequence: 7 }], targets: [],
        resilience: {
          aggregate_health: "DEGRADED", competition_locked: true, failure_injection_enabled: false,
          hardware_gate_h1: { gate_id: "H1_PHYSICAL_PROTOTYPE", name: "Physical prototype", state: "PENDING", reason: "Unavailable" },
        },
      },
    });
    const state = useGcsStore.getState();
    expect(state.mission?.name).toBe("Recovered");
    expect(state.vehicles.drone_01.sequence).toBe(7);
    expect(state.resilienceStatus?.aggregate_health).toBe("DEGRADED");
    expect(state.hardwareGateH1?.state).toBe("PENDING");
  });

  it("stores the camera frame payload emitted by the surveillance service", () => {
    useGcsStore.getState().applyEnvelope({
      type: "camera_frame", timestamp: new Date().toISOString(), mission_id: "mission",
      vehicle_id: "drone_01", sequence: 0,
      payload: {
        image_b64: "jpeg-bytes", source_ts: Date.now() / 1000,
        frame_count: 1, decoded_frame_count: 1, inference_count: 1,
        detections: [], model_status: "READY",
      },
    });
    expect(useGcsStore.getState().cameraFrame?.image_b64).toBe("jpeg-bytes");
    expect(useGcsStore.getState().cameraFrame?.decoded_frame_count).toBe(1);
  });

  it("replaces detections with the newest frame and clears them for a new mission", () => {
    const state = useGcsStore.getState();
    state.applyEnvelope({
      type: "camera_frame", timestamp: new Date().toISOString(), mission_id: "mission-a",
      vehicle_id: "drone_01", sequence: 1,
      payload: {
        image_b64: "first-frame", source_ts: Date.now() / 1000, frame_id: "camera:1",
        detections: [{ class_id: 0, display_label: "person", conf: 0.9, bbox: [10, 20, 30, 40] }],
      },
    });
    state.applyEnvelope({
      type: "camera_frame", timestamp: new Date().toISOString(), mission_id: "mission-a",
      vehicle_id: "drone_01", sequence: 2,
      payload: { image_b64: "second-frame", source_ts: Date.now() / 1000, frame_id: "camera:2", detections: [] },
    });
    expect(useGcsStore.getState().cameraFrame?.frame_id).toBe("camera:2");
    expect(useGcsStore.getState().cameraFrame?.detections).toEqual([]);

    useGcsStore.getState().setMission({
      id: "mission-b", name: "New run", state: "DRAFT", created_at: new Date().toISOString(), elapsed_seconds: 0, coverage_percent: 0,
    });
    expect(useGcsStore.getState().cameraFrame).toBeNull();
  });
});
