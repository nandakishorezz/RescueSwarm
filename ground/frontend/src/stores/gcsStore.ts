import { create } from "zustand";

import type {
  ApplicationMode, ConnectionState, CoveragePlan, CoverageRequest, GeoPoint, HardwareGateStatus,
  Mission, MissionEvent, OfflineStatus, RealtimeEnvelope, RescueState, Target, VehicleState,
  FleetStatus, ResilienceStatus,
  DigitalTwinSnapshot, ReplayBundle, ScaleMode, TargetObservationView, ViewMode,
} from "../types/domain";

export const CONNECTED_SECONDS = 2;
export const DEGRADED_SECONDS = 5;

export function classifyTelemetryAge(
  ageSeconds: number,
  connectedSeconds = CONNECTED_SECONDS,
  degradedSeconds = DEGRADED_SECONDS,
): ConnectionState {
  if (ageSeconds < connectedSeconds) return "CONNECTED";
  if (ageSeconds <= degradedSeconds) return "DEGRADED";
  return "STALE";
}

function storedRecord(key: string, fallback: Record<string, boolean>): Record<string, boolean> {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    return value && typeof value === "object" && !Array.isArray(value) ? { ...fallback, ...value } : fallback;
  } catch {
    return fallback;
  }
}

interface GcsState {
  mode: ApplicationMode;
  competitionMode: boolean;
  backendState: ConnectionState;
  websocketState: ConnectionState;
  error: string | null;
  mission: Mission | null;
  coveragePlan: CoveragePlan | null;
  executionSource: "SIMULATED" | "SITL" | "REAL";
  coverageDefaults: CoverageRequest | null;
  rescueState: RescueState;
  hardwareGateH1: HardwareGateStatus | null;
  guidedApproachRoute: GeoPoint[];
  payloadRemaining: number | null;
  resilienceStatus: ResilienceStatus | null;
  fleetStatus: FleetStatus | null;
  digitalTwin: DigitalTwinSnapshot | null;
  replay: ReplayBundle | null;
  viewMode: ViewMode;
  scaleMode: ScaleMode;
  selectedVehicleId: string | null;
  selectedTargetId: string | null;
  selectedObservationId: string | null;
  vehicles: Record<string, VehicleState>;
  trails: Record<string, [number, number][]>;
  targets: Record<string, Target>;
  events: MissionEvent[];
  offlineStatus: OfflineStatus | null;
  lastEnvelope: RealtimeEnvelope | null;
  selectedCoordinate: [number, number] | null;
  connectedThresholdSeconds: number;
  degradedThresholdSeconds: number;
  maxAreaHectares: number | null;
  trailMaxPoints: number;
  recentEventsMax: number;
  splitRatio: "35_65" | "50_50" | "65_35";
  missionRailOpen: boolean;
  panelVisibility: Record<string, boolean>;
  digitalTwinLayers: Record<string, boolean>;
  cameraFrame: any | null;
  setMode: (mode: ApplicationMode) => void;
  setCompetitionMode: (enabled: boolean) => void;
  setBackendState: (state: ConnectionState) => void;
  setWebsocketState: (state: ConnectionState) => void;
  setError: (error: string | null) => void;
  setMission: (mission: Mission) => void;
  setCoveragePlan: (plan: CoveragePlan | null) => void;
  setCoverageDefaults: (configuration: CoverageRequest) => void;
  setHardwareGateH1: (gate: HardwareGateStatus) => void;
  setResilienceStatus: (status: ResilienceStatus) => void;
  setFleetStatus: (status: FleetStatus) => void;
  setDigitalTwin: (snapshot: DigitalTwinSnapshot | null) => void;
  setReplay: (replay: ReplayBundle | null) => void;
  setViewMode: (mode: ViewMode) => void;
  setScaleMode: (mode: ScaleMode) => void;
  selectVehicle: (id: string | null) => void;
  selectTarget: (id: string | null) => void;
  selectObservation: (id: string | null) => void;
  clearSimulationState: () => void;
  setInitialData: (vehicles: VehicleState[], targets: Target[], events: MissionEvent[]) => void;
  setOfflineStatus: (status: OfflineStatus) => void;
  setSelectedCoordinate: (point: [number, number] | null) => void;
  setStaleThresholds: (connected: number, degraded: number) => void;
  setRuntimeLimits: (areaHectares: number, trailPoints: number, recentEvents: number) => void;
  setSplitRatio: (ratio: "35_65" | "50_50" | "65_35") => void;
  setMissionRailOpen: (open: boolean) => void;
  setPanelVisibility: (id: string, open: boolean) => void;
  setDigitalTwinLayer: (id: string, visible: boolean) => void;
  applyEnvelope: (message: RealtimeEnvelope) => void;
  refreshLinkStates: (now?: number) => void;
}

export const useGcsStore = create<GcsState>((set) => ({
  mode: "SETUP",
  competitionMode: false,
  backendState: "CONNECTING",
  websocketState: "DISCONNECTED",
  error: null,
  mission: null,
  coveragePlan: null,
  executionSource: "SIMULATED",
  coverageDefaults: null,
  rescueState: "IDLE",
  hardwareGateH1: null,
  guidedApproachRoute: [],
  payloadRemaining: null,
  resilienceStatus: null,
  fleetStatus: null,
  digitalTwin: null,
  replay: null,
  viewMode: "2D",
  scaleMode: "ICON",
  selectedVehicleId: null,
  selectedTargetId: null,
  selectedObservationId: null,
  vehicles: {},
  trails: {},
  targets: {},
  events: [],
  offlineStatus: null,
  lastEnvelope: null,
  selectedCoordinate: null,
  connectedThresholdSeconds: CONNECTED_SECONDS,
  degradedThresholdSeconds: DEGRADED_SECONDS,
  maxAreaHectares: null,
  trailMaxPoints: 5000,
  recentEventsMax: 100,
  splitRatio: (localStorage.getItem("rescueswarm.splitRatio") as GcsState["splitRatio"]) || "50_50",
  missionRailOpen: localStorage.getItem("rescueswarm.missionRailOpen") !== "false",
  panelVisibility: storedRecord("rescueswarm.panelVisibility", { fleet: true, vehicles: true, targets: true, health: false, video: false, timeline: true }),
  digitalTwinLayers: storedRecord("rescueswarm.digitalTwinLayers", { aircraft: true, paths: true, sectors: true, cameraFrustum: true, cameraFootprint: true, detectionRays: true, targets: true, uncertainty: true, axes: false }),
  cameraFrame: null,
  setMode: (mode) => set((state) => state.competitionMode && (mode === "ENGINEERING" || mode === "SIMULATION") ? state : { mode }),
  setCompetitionMode: (competitionMode) => set((state) => ({
    competitionMode,
    mode: competitionMode && (state.mode === "ENGINEERING" || state.mode === "SIMULATION") ? "SETUP" : state.mode,
  })),
  setBackendState: (backendState) => set({ backendState }),
  setWebsocketState: (websocketState) => set({ websocketState }),
  setError: (error) => set({ error }),
  setMission: (mission) => set((state) => (state.mission?.id === mission?.id ? { mission } : { 
    mission, 
    vehicles: {}, 
    trails: {}, 
    targets: {}, 
    events: [],
    cameraFrame: null,
    rescueState: "IDLE" as RescueState,
  })),
  setCoveragePlan: (coveragePlan) => set({ coveragePlan }),
  setCoverageDefaults: (coverageDefaults) => set({ coverageDefaults }),
  setHardwareGateH1: (hardwareGateH1) => set({ hardwareGateH1 }),
  setResilienceStatus: (resilienceStatus) => set({ resilienceStatus }),
  setFleetStatus: (fleetStatus) => set({ fleetStatus }),
  setDigitalTwin: (digitalTwin) => set({ digitalTwin }),
  setReplay: (replay) => set({ replay }),
  setViewMode: (viewMode) => set({ viewMode }),
  setScaleMode: (scaleMode) => set({ scaleMode }),
  selectVehicle: (selectedVehicleId) => set({ selectedVehicleId }),
  selectTarget: (selectedTargetId) => set({ selectedTargetId }),
  selectObservation: (selectedObservationId) => set({ selectedObservationId }),
  clearSimulationState: () => set({ mission: null, coveragePlan: null, vehicles: {}, trails: {}, targets: {}, events: [], cameraFrame: null }),
  setInitialData: (vehicles, targets, events) => set((state) => ({
    vehicles: Object.fromEntries(vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle])),
    targets: Object.fromEntries(targets.map((target) => [target.target_id, target])),
    events: events.slice(-state.recentEventsMax),
  })),
  setOfflineStatus: (offlineStatus) => set({ offlineStatus }),
  setSelectedCoordinate: (selectedCoordinate) => set({ selectedCoordinate }),
  setStaleThresholds: (connectedThresholdSeconds, degradedThresholdSeconds) => set({
    connectedThresholdSeconds,
    degradedThresholdSeconds,
  }),
  setRuntimeLimits: (maxAreaHectares, trailMaxPoints, recentEventsMax) => set({
    maxAreaHectares,
    trailMaxPoints,
    recentEventsMax,
  }),
  setSplitRatio: (splitRatio) => { localStorage.setItem("rescueswarm.splitRatio", splitRatio); set({ splitRatio }); },
  setMissionRailOpen: (missionRailOpen) => { localStorage.setItem("rescueswarm.missionRailOpen", String(missionRailOpen)); set({ missionRailOpen }); },
  setPanelVisibility: (id, open) => set((state) => {
    const panelVisibility = { ...state.panelVisibility, [id]: open };
    localStorage.setItem("rescueswarm.panelVisibility", JSON.stringify(panelVisibility));
    return { panelVisibility };
  }),
  setDigitalTwinLayer: (id, visible) => set((state) => {
    const digitalTwinLayers = { ...state.digitalTwinLayers, [id]: visible };
    localStorage.setItem("rescueswarm.digitalTwinLayers", JSON.stringify(digitalTwinLayers));
    return { digitalTwinLayers };
  }),
  applyEnvelope: (message) => set((state) => {
    // A socket from a previous mission can close after the new mission has
    // already populated this store.  Its delayed target/frame events must not
    // resurrect old survivor IDs or stale camera imagery.
    if (state.mission && message.mission_id !== state.mission.id && (
      message.type === "camera_frame" || message.type.startsWith("target.")
    )) return state;
    if (message.type === "camera_frame") {
      return { lastEnvelope: message, cameraFrame: message.payload };
    }
    if (message.type === "system.snapshot") {
      const payload = message.payload as unknown as {
        mission?: Mission; vehicles?: VehicleState[]; targets?: Target[]; resilience?: ResilienceStatus; fleet?: FleetStatus;
      };
      return {
        lastEnvelope: message,
        mission: payload.mission ?? state.mission,
        vehicles: payload.vehicles ? Object.fromEntries(payload.vehicles.map((item) => {
          const current = state.vehicles[item.vehicle_id];
          return [item.vehicle_id, { ...item, _receive_time_ms: current?._receive_time_ms }];
        })) : state.vehicles,
        targets: payload.targets ? Object.fromEntries(payload.targets.map((item) => [item.target_id, item])) : state.targets,
        resilienceStatus: payload.resilience ?? state.resilienceStatus,
        hardwareGateH1: payload.resilience?.hardware_gate_h1 ?? state.hardwareGateH1,
        fleetStatus: payload.fleet ?? state.fleetStatus,
      };
    }
    if (message.type === "system.safety" || message.type === "system.recovery") {
      return { lastEnvelope: message, resilienceStatus: message.payload as unknown as ResilienceStatus };
    }
    if (message.type.startsWith("fleet.")) {
      return { lastEnvelope: message, fleetStatus: message.payload as unknown as FleetStatus };
    }
    if (message.type === "vehicle.telemetry") {
      const vehicle = message.payload as unknown as VehicleState;
      const current = state.vehicles[vehicle.vehicle_id];
      if (current && vehicle.sequence <= current.sequence) return state;
      let trail = state.trails[vehicle.vehicle_id] ?? [];
      if (trail.length === 0) {
        trail = [[vehicle.longitude, vehicle.latitude]];
      } else {
        const lastPt = trail[trail.length - 1];
        // Deduplicate if the drone hasn't moved significantly (e.g. at least 1e-6 degrees)
        if (Math.abs(lastPt[0] - vehicle.longitude) > 1e-6 || Math.abs(lastPt[1] - vehicle.latitude) > 1e-6) {
          trail = [...trail, [vehicle.longitude, vehicle.latitude]];
        }
      }
      trail = trail.slice(-state.trailMaxPoints);
      const source = (message.payload.source as "SIMULATED" | "SITL" | "REAL" | undefined)
        ?? (vehicle.simulated ? "SIMULATED" : "SITL");
      return {
        lastEnvelope: message,
        executionSource: source,
        vehicles: { ...state.vehicles, [vehicle.vehicle_id]: { ...vehicle, linkState: "CONNECTED", _receive_time_ms: performance.now() } },
        trails: { ...state.trails, [vehicle.vehicle_id]: trail },
      };
    }
    if (message.type === "mission.progress" && state.mission) {
      const executionSource = message.payload.execution_source as "SIMULATED" | "SITL" | "REAL" | undefined;
      return {
        lastEnvelope: message,
        executionSource: executionSource ?? state.executionSource,
        rescueState: (message.payload.rescue_state as RescueState | undefined) ?? state.rescueState,
        payloadRemaining: typeof message.payload.payload_remaining === "number"
          ? message.payload.payload_remaining : state.payloadRemaining,
        mission: { ...state.mission, ...message.payload } as Mission,
      };
    }
    if (message.type === "rescue.state_changed") {
      const event = message.payload as unknown as MissionEvent & { metadata?: Record<string, unknown> };
      const current = event.metadata?.current_state ?? event.metadata?.next_state;
      return {
        lastEnvelope: message,
        rescueState: (typeof current === "string" ? current : state.rescueState) as RescueState,
        events: [...state.events, event].slice(-state.recentEventsMax),
      };
    }
    if (["target.created", "target.updated", "target.candidate", "target.confirmed", "target.rejected"].includes(message.type)) {
      const target = message.payload as unknown as Target;
      if (state.targets[target.target_id]?.state === "REJECTED" && target.state !== "REJECTED") return state;
      return { lastEnvelope: message, targets: { ...state.targets, [target.target_id]: target } };
    }
    if (message.type === "target.observation") {
      const payload = message.payload as unknown as Partial<TargetObservationView> & { observation_id: string };
      if (!state.digitalTwin || !payload.camera_ray || !payload.world_ray_enu || !payload.target_enu_m) {
        return { lastEnvelope: message };
      }
      const observation = {
        target_id: "UNASSOCIATED", vehicle_id: message.vehicle_id ?? "UNKNOWN", frame_id: "UNKNOWN",
        capture_timestamp: message.timestamp, chosen_pixel: [0, 0], pixel_strategy: "BBOX_CENTER",
        body_ray: payload.camera_ray, range_m: 0, detection_confidence: 0,
        position_uncertainty_m: 0, evidence_class: "SYNTHETIC", ...payload,
      } as TargetObservationView;
      const observations = [...state.digitalTwin.observations.filter((item) => item.observation_id !== observation.observation_id), observation].slice(-100);
      return { lastEnvelope: message, digitalTwin: { ...state.digitalTwin, observations } };
    }
    if (message.type === "mission.state" || message.type === "system.warning") {
      const event = message.payload as unknown as MissionEvent;
      const metadata = (message.payload.metadata ?? {}) as Record<string, unknown>;
      const route = event.type === "APPROACH_STARTED" && Array.isArray(metadata.path_wgs84)
        ? metadata.path_wgs84 as GeoPoint[] : state.guidedApproachRoute;
      return { lastEnvelope: message, guidedApproachRoute: route, events: [...state.events, event].slice(-state.recentEventsMax) };
    }
    return { lastEnvelope: message };
  }),
  refreshLinkStates: (now = Date.now()) => set((state) => ({
    vehicles: Object.fromEntries(Object.entries(state.vehicles).map(([id, vehicle]) => {
      const age = (now - new Date(vehicle.last_update).getTime()) / 1000;
      const linkState = vehicle.connected
        ? classifyTelemetryAge(age, state.connectedThresholdSeconds, state.degradedThresholdSeconds)
        : "DISCONNECTED";
      return [id, { ...vehicle, linkState }];
    })),
  })),
}));
