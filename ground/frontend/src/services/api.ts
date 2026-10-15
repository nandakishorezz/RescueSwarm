import type { CoveragePlan, CoverageRequest, DigitalTwinSnapshot, FleetStatus, FlightParameters, Mission, MissionEvent, OfflineStatus, ReplayBundle, Target, VehicleState } from "../types/domain";

const origin = import.meta.env.VITE_API_ORIGIN || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${origin}${path}`, init);
  if (!response.ok) {
    let reason = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as { detail?: unknown };
      if (body.detail) {
        if (typeof body.detail === "string") {
          reason = body.detail;
        } else if (Array.isArray(body.detail)) {
          // FastAPI validation error: [{loc, msg, type},...]
          reason = body.detail.map((e: unknown) => {
            if (e && typeof e === "object" && "msg" in e) return String((e as {msg: unknown}).msg);
            return JSON.stringify(e);
          }).join("; ");
        } else {
          reason = JSON.stringify(body.detail);
        }
      }
    } catch {
      // Non-JSON errors retain the HTTP reason.
    }
    throw new Error(reason);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Record<string, unknown>>("/api/health"),
  system: () => request<Record<string, unknown>>("/api/system/status"),
  offline: () => request<OfflineStatus>("/api/system/offline-status"),
  geolocationStatus: () => request<Record<string, unknown>>("/api/system/geolocation-status"),
  hardwareValidation: () => request<Record<string, unknown>>("/api/system/hardware-validation"),
  missions: () => request<Mission[]>("/api/missions"),
  mission: (id: string) => request<Mission>(`/api/missions/${id}`),
  createMission: (name: string) => request<Mission>("/api/missions", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  }),
  uploadBoundary: (id: string, file: File) => {
    const form = new FormData();
    form.append("boundary_file", file);
    return request<Mission>(`/api/missions/${id}/boundary`, { method: "POST", body: form });
  },
  validateMission: (id: string) => request<Mission>(`/api/missions/${id}/validate`, { method: "POST" }),
  plan: (id: string) => request<CoveragePlan>(`/api/missions/${id}/plan`),
  generatePlan: (id: string, configuration: CoverageRequest) => request<CoveragePlan>(`/api/missions/${id}/plan`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(configuration),
  }),
  flightParameters: (id: string) => request<FlightParameters>(`/api/missions/${id}/flight-parameters`),
  updateFlightParameters: (id: string, params: FlightParameters) => request<FlightParameters>(
    `/api/missions/${id}/flight-parameters`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params),
    },
  ),
  vehicles: (id: string) => request<VehicleState[]>(`/api/missions/${id}/vehicles`),
  targets: (id: string) => request<Target[]>(`/api/missions/${id}/targets`),
  events: (id: string) => request<MissionEvent[]>(`/api/missions/${id}/events`),
  review: (id: string) => request<Record<string, unknown>>(`/api/missions/${id}/review`),
  digitalTwin: (id: string) => request<DigitalTwinSnapshot>(`/api/missions/${id}/digital-twin`),
  replay: (id: string) => request<ReplayBundle>(`/api/missions/${id}/replay`),
  artifacts: (id: string) => request<{ mission_id: string; artifacts: { name: string; url: string }[] }>(`/api/missions/${id}/artifacts`),
  fleet: (id: string) => request<FleetStatus>(`/api/missions/${id}/fleet`),
  fleetPlan: (id: string, fleetSize = 2) => request<Record<string, unknown>>(`/api/engineering/fleet/missions/${id}/plan`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fleet_size: fleetSize }),
  }),
  simulator: (id: string, action: string) => request<Record<string, unknown>>(
    `/api/engineering/simulator/${id}/${action}`, { method: "POST" },
  ),
  sitlStatus: (id?: string) => request<Record<string, unknown>>(`/api/engineering/sitl/status${id ? `?mission_id=${id}` : ""}`),
  startSitl: (missionId: string, launchAltitudeM: number) => request<Record<string, unknown>>(
    "/api/engineering/sitl/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mission_id: missionId, launch_altitude_m: launchAltitudeM, heading_deg: 0 }),
    },
  ),
  stopSitl: () => request<Record<string, unknown>>("/api/engineering/sitl/stop", { method: "POST" }),
  executeSitl: (id: string) => request<Record<string, unknown>>(`/api/engineering/sitl/missions/${id}/execute`, { method: "POST" }),
  abortSitl: (id: string) => request<Record<string, unknown>>(`/api/engineering/sitl/missions/${id}/abort`, { method: "POST" }),
  executeSitlRescue: (id: string, eastM: number, northM: number) => request<Record<string, unknown>>(
    `/api/engineering/sitl/missions/${id}/rescue/execute`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_id: "synthetic_survivor_01", true_east_m: eastM, true_north_m: northM,
        visibility_distance_m: 40, detection_probability: 1, pixel_noise_stddev: 0,
        delivery_required: true, seed: 7, auto_start_sitl: true,
      }),
    },
  ),
  visionStatus: () => request<Record<string, unknown>>("/api/engineering/vision/status"),
  resilienceStatus: (id?: string) => request<Record<string, unknown>>(`/api/system/resilience${id ? `?mission_id=${id}` : ""}`),
  resilienceScenarios: () => request<Record<string, unknown>[]>("/api/engineering/failures/scenarios"),
  runFailureScenario: (scenarioId: string) => request<Record<string, unknown>>(
    `/api/engineering/failures/scenarios/${scenarioId}/run`, { method: "POST" },
  ),
  operatorAction: (missionId: string, action: string, missionState: string) => request<Record<string, unknown>>(
    `/api/operator/missions/${missionId}/actions/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mission_state: missionState }),
    },
  ),
  simulationStatus: () => request<Record<string, unknown>>("/api/simulation/status"),
  multiDroneStatus: () => request<import("../features/multiDrone/types").FleetRuntimeStatus>("/api/multidrone/status"),
  startMultiDrone: (configuration: Record<string, unknown>) => request<import("../features/multiDrone/types").FleetRuntimeStatus>("/api/multidrone/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(configuration),
  }),
  stopMultiDrone: () => request<import("../features/multiDrone/types").FleetRuntimeStatus>("/api/multidrone/stop", { method: "POST" }),
  returnMultiDroneVehicle: (id: string) => request<import("../features/multiDrone/types").FleetRuntimeStatus>(`/api/multidrone/vehicles/${id}/return`, { method: "POST" }),
  acknowledgeMultiDroneAssignment: (assignmentId: string) => request<Record<string, unknown>>(`/api/multidrone/assignments/${assignmentId}/acknowledge`, { method: "POST" }),
  setMultiDroneSurvivorPriority: (canonicalId: string, priority: "CRITICAL" | "HIGH" | "NORMAL" | "LOW") => request<Record<string, unknown>>(
    `/api/multidrone/survivors/${encodeURIComponent(canonicalId)}/priority`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ priority }),
    },
  ),
  cancelMultiDroneAssignment: (assignmentId: string) => request<Record<string, unknown>>(`/api/multidrone/assignments/${assignmentId}/cancel`, { method: "POST" }),
  simulationReferenceProfiles: () => request<Record<string, unknown>>("/api/simulation/profiles/reference"),
  prepareSimulation: (definition: Record<string, unknown>) => request<Record<string, unknown>>("/api/simulation/prepare", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(definition),
  }),
  startSimulation: () => request<Record<string, unknown>>("/api/simulation/start", { method: "POST" }),
  stopSimulation: () => request<Record<string, unknown>>("/api/simulation/stop", { method: "POST" }),
  simulationObserverTruth: () => request<Record<string, unknown>>("/api/simulation/observer/truth?observer_ground_truth=true"),
  simulationPerceptionLatest: () => request<Record<string, unknown>>("/api/simulation/perception/latest"),
  simulationPerceptionObserver: () => request<Record<string, unknown>>("/api/simulation/observer/perception/latest?observer_ground_truth=true"),
  simulationEnuToWorld: (payload: Record<string, number>) => request<Record<string, unknown>>("/api/simulation/georeference/enu-to-world", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
};

export function simulationCameraFrameUrl(overlay = true, nonce = Date.now()): string {
  return `${origin}/api/simulation/perception/latest/frame?overlay=${overlay ? "true" : "false"}&v=${nonce}`;
}

export function websocketUrl(missionId: string): string {
  if (origin) {
    const parsed = new URL(origin);
    const scheme = parsed.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${parsed.host}/ws/mission/${missionId}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws/mission/${missionId}`;
}
