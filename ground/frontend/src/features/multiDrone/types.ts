export type DroneRole = "SEARCHER" | "SERVICER";

export type FleetState =
  | "IDLE"
  | "VALIDATING"
  | "STARTING"
  | "WAITING_FOR_TELEMETRY"
  | "RUNNING"
  | "DEGRADED"
  | "RETURNING"
  | "STOPPING"
  | "STOPPED"
  | "ERROR";

export type DroneLifecycleState =
  | "CONFIGURED"
  | "STARTING"
  | "WAITING_FOR_HEARTBEAT"
  | "READY"
  | "SEARCHING"
  | "RETURNING"
  | "LANDING"
  | "LANDED"
  | "DISARMED"
  | "FAILED"
  | "STOPPED";

export interface FleetVehicleView {
  vehicle_id: string;
  role: DroneRole;
  system_id: number;
  connection: string;
  state: DroneLifecycleState | string;
  state_updated_at?: string;
  connected: boolean;
  telemetry_age_s: number | null;
  telemetry: {
    latitude: number;
    longitude: number;
    relative_altitude_m: number;
    ground_speed_m_s: number;
    flight_mode: string;
    armed: boolean;
    gps_fix_type: number;
    battery_percent?: number;
  } | null;
  progress_percent: number;
  sector_ids?: string[];
  route_configuration?: { search_altitude_m?: number; search_speed_m_s?: number };
  flight_parameters_snapshot?: Record<string, number> | null;
  camera_model_status?: string;
  camera_observed_rtf?: number;
  rtl_budget_s?: number;
  current_mission_item?: number;
  total_mission_items?: number;
  camera_status: "LIVE" | "STALE" | "NO_FRAMES";
  camera_frame_count: number;
  camera_decoded_count: number;
  camera_inference_count: number;
  camera_age_s: number | null;
  last_event: string | null;
  error?: string | null;
}

export interface FleetLifecycleEvent {
  timestamp?: string;
  vehicle_id?: string | null;
  type: string;
  message?: string;
  severity?: "INFO" | "WARNING" | "ERROR" | "CRITICAL" | string;
  metadata?: Record<string, unknown>;
}

export interface SharedSurvivorView {
  canonical_id: string;
  session_id: string;
  confirmation_status: string;
  observation_count: number;
  source_drone_ids: string[];
  source_track_ids?: string[];
  first_seen_at?: string;
  confirmed_at?: string;
  priority?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW";
  priority_source?: "DEFAULT" | "OPERATOR" | "MISSION";
  last_seen_at?: string;
  latitude?: number | null;
  longitude?: number | null;
  east_m?: number | null;
  north_m?: number | null;
  uncertainty_m?: number | null;
  confidence?: number;
  inlier_count?: number;
  evidence_references?: string[];
  assignment_id?: string | null;
  assigned_servicer_id?: string | null;
  assignment_state?: string | null;
  delivery_status?: string | null;
}

export type ServiceAssignmentState =
  | "QUEUED"
  | "ASSIGNED"
  | "ACKNOWLEDGED"
  | "APPROACHING"
  | "HOLDING_FOR_RELEASE"
  | "RELEASED"
  | "VERIFYING"
  | "DELIVERED"
  | "RESUMING_SEARCH"
  | "COMPLETED"
  | "CANCELLED"
  | "FAILED";

export interface ServiceAssignmentView {
  assignment_id: string;
  session_id: string;
  canonical_survivor_id: string;
  servicer_id?: string | null;
  state: ServiceAssignmentState | string;
  reason?: string | null;
  priority?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW";
  priority_source?: "DEFAULT" | "OPERATOR" | "MISSION";
  queue_position?: number | null;
  created_at?: string;
  updated_at?: string;
  lease_expires_at?: string | null;
  payload_released?: boolean;
  delivery_verified?: boolean;
  release_id?: string | null;
  delivery_evidence?: Record<string, unknown> | null;
  checkpoint_seq?: number | null;
  resume_seq?: number | null;
  standoff_distance_m?: number | null;
  failure_reason?: string | null;
}

export interface FleetRuntimeStatus {
  state: FleetState | string;
  state_reason?: string;
  state_updated_at?: string;
  session: { session_id: string; mission_id: string; run_id: string; configuration: unknown } | null;
  vehicles: FleetVehicleView[];
  failed_vehicles?: string[];
  trails?: Record<string, [number, number][]>;
  targets: SharedSurvivorView[];
  assignments: ServiceAssignmentView[];
  deliveries: unknown[];
  events: FleetLifecycleEvent[];
  error: string | null;
  partition?: unknown;
  coordination_state?: string;
}

export interface FleetCameraFrame {
  image_b64: string;
  source_ts: number;
  frame_id: string;
  frame_count: number;
  detections: { class_id: number; display_label: string; conf: number; bbox: number[] }[];
  model_status: string;
}
