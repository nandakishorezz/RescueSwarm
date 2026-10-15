export type ApplicationMode = "ENGINEERING" | "SETUP" | "MISSION" | "SIMULATION" | "MULTIDRONE" | "REVIEW";
export type MissionState =
  | "DRAFT" | "VALIDATED" | "READY" | "RUNNING" | "RETURNING"
  | "COMPLETED" | "ABORTED" | "FAILED";
export type TargetState =
  | "CANDIDATE" | "CONFIRMED" | "ASSIGNED" | "APPROACHING"
  | "DELIVERING" | "DELIVERED" | "REJECTED" | "UNSERVICEABLE" | "AMBIGUOUS";
export type DeliveryState =
  | "NOT_REQUIRED" | "PENDING" | "DELIVERY_QUEUED" | "APPROACHING" | "RELEASED"
  | "IN_PROGRESS" | "DELIVERED" | "DELIVERED_VERIFIED" | "FAILED" | "DEFERRED" | "AMBIGUOUS";
export type ConnectionState = "CONNECTING" | "RECONNECTING" | "CONNECTED" | "DEGRADED" | "STALE" | "DISCONNECTED";
export type VideoState = "OFFLINE" | "CONNECTING" | "LIVE" | "DEGRADED" | "ERROR" | "SIMULATED";
export type EvidenceClass =
  | "PHYSICAL_MEASURED" | "PHYSICAL_UNVERIFIED" | "SITL"
  | "SYNTHETIC" | "CONFIGURED_ASSUMPTION" | "UNKNOWN";
export type ViewMode = "2D" | "3D" | "SPLIT";
export type ScaleMode = "ICON" | "TRUE_SCALE";

export interface FlightParameters {
  search_altitude_m: number;
  search_speed_m_s: number;
  transit_speed_m_s: number;
  approach_altitude_m: number;
  approach_speed_m_s: number;
  delivery_altitude_m: number;
  delivery_stabilization_s: number;
  safe_drop_offset_m: number;
  search_swath_overlap_percent: number;
  rtl_altitude_m: number;
  rtl_speed_m_s: number;
  payload_slots: number;
}
export type RescueState =
  | "IDLE" | "PREPARING" | "TAKEOFF" | "SEARCHING" | "TARGET_CANDIDATE"
  | "TARGET_CONFIRMED" | "SEARCH_SUSPENDING" | "APPROACH_PLANNING" | "GUIDED_APPROACH"
  | "REACQUIRING" | "PRECISION_ALIGNING" | "DROP_READY" | "DELIVERING"
  | "DELIVERY_VERIFYING" | "SEARCH_RESUMING" | "RETURNING" | "LANDING"
  | "COMPLETED" | "ABORTING" | "ABORTED" | "FAILED";

export interface HardwareGateStatus {
  gate_id: string;
  name: string;
  state: "PENDING" | "PASSED" | "FAILED";
  reason: string;
}

export type AggregateHealth = "READY" | "DEGRADED" | "NOT_READY" | "EMERGENCY";
export interface SafetyDecision {
  severity: "INFO" | "DEGRADED" | "WARNING" | "CRITICAL" | "EMERGENCY";
  reason: string;
  source: string;
  recommended_action: string;
  authoritative_layer: "AUTOPILOT" | "AUTONOMY" | "OPERATOR";
  timestamp: string;
  evidence: Record<string, unknown>;
}
export interface ResilienceStatus {
  aggregate_health: AggregateHealth;
  safety_decision?: SafetyDecision | null;
  recovery?: Record<string, unknown> | null;
  competition_locked: boolean;
  failure_injection_enabled: boolean;
  hardware_gate_h1: HardwareGateStatus;
  competition_readiness?: { overall: string };
}

export interface FleetStatus {
  mission_id: string;
  state: string;
  fleet_size: number;
  vehicle_ids?: string[];
  hardware_gate_h1: "PENDING";
  execution_source: "SITL_ONLY";
  partition?: { imbalance_percent?: number; estimated_makespan_s?: number; per_vehicle_cost?: Record<string, number> };
  progress?: { combined_coverage_percent?: number; per_vehicle_coverage_percent?: Record<string, number> };
  payload_inventory?: Record<string, number>;
  latest_run?: string | null;
  video?: Record<string, VideoState>;
}

export interface GeoPoint {
  latitude: number;
  longitude: number;
  altitude_m?: number | null;
}

export interface Boundary {
  vertices: GeoPoint[];
  source_format: string;
  area_m2: number;
  area_hectares: number;
  origin: GeoPoint;
  warnings: string[];
}

export interface Mission {
  id: string;
  name: string;
  state: MissionState;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  boundary?: Boundary | null;
  area_m2?: number | null;
  elapsed_seconds: number;
  coverage_percent: number;
  flight_parameters?: FlightParameters | null;
}

export interface VehicleState {
  vehicle_id: string;
  name: string;
  connected: boolean;
  latitude: number;
  longitude: number;
  relative_altitude_m: number;
  absolute_altitude_m?: number | null;
  agl_altitude_m?: number | null;
  roll_deg: number;
  pitch_deg: number;
  yaw_deg: number;
  ground_speed_m_s: number;
  vertical_speed_m_s?: number | null;
  velocity_enu_m_s?: [number, number, number] | null;
  quaternion_body_to_enu?: [number, number, number, number] | null;
  battery_percent: number;
  battery_voltage: number;
  gps_fix_type: number;
  satellites: number;
  flight_mode: string;
  armed: boolean;
  link_quality: number;
  current_task: string;
  last_update: string;
  sequence: number;
  current_mission_item?: number;
  total_mission_items?: number;
  simulated: boolean;
  linkState?: ConnectionState;
  evidence_class?: EvidenceClass;
  _receive_time_ms?: number;
}

export interface Target {
  target_id: string;
  mission_id: string;
  state: TargetState;
  latitude: number;
  longitude: number;
  east_m: number;
  north_m: number;
  detection_confidence: number;
  geolocation_uncertainty_m: number;
  observation_count: number;
  first_seen_at: string;
  last_seen_at: string;
  delivery_state: DeliveryState;
  assigned_vehicle_id?: string | null;
  simulated: boolean;
  geolocation_quality?: string | null;
  inlier_count?: number | null;
  outlier_count?: number | null;
  evidence_references?: string[];
  observing_vehicle_ids?: string[];
  delivery_point_east_m?: number | null;
  delivery_point_north_m?: number | null;
}

export interface MissionEvent {
  event_id: string;
  mission_id: string;
  vehicle_id?: string | null;
  target_id?: string | null;
  type: string;
  severity: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  timestamp: string;
  message: string;
  simulated: boolean;
  metadata?: Record<string, unknown>;
}

export interface RealtimeEnvelope {
  type: string;
  timestamp: string;
  mission_id: string;
  vehicle_id: string | null;
  sequence: number;
  payload: Record<string, unknown>;
}

export interface OfflineStatus {
  status: "READY" | "FAILED";
  external_network_required: boolean;
  external_tile_sources: number;
  external_api_dependencies: number;
  local_static_assets: string;
  local_database: string;
  local_websocket: string;
  competition_mode: boolean;
  violations: string[];
}

export type SweepStrategy = "AUTO_OPTIMIZED" | "FIXED_ANGLE";
export type PathKind = "SEARCH" | "TRANSIT";
export type ExecutionSource = "SIMULATED" | "SITL" | "REAL";

export interface EnuCoordinate { east_m: number; north_m: number; }
export interface CoverageSegment {
  segment_id: string;
  kind: PathKind;
  points_enu: EnuCoordinate[];
  points_wgs84: GeoPoint[];
  assigned_vehicle_id?: string | null;
  search_line_index?: number | null;
}
export interface CoverageRequest {
  search_altitude_m: number;
  lane_spacing_m: number;
  effective_swath_m: number;
  along_track_footprint_m?: number;
  edge_margin_m: number;
  search_speed_m_s: number;
  sweep_strategy: SweepStrategy;
  fixed_sweep_angle_deg: number;
  turn_penalty_m: number;
  min_planned_coverage_percent: number;
  max_waypoints: number;
  flight_parameters?: FlightParameters | null;
}
export interface CoveragePlan {
  mission_id: string;
  planner_version: string;
  configuration: CoverageRequest;
  safe_polygon_enu: EnuCoordinate[];
  safe_polygon_wgs84: GeoPoint[];
  sweep_angle_deg: number;
  segments: CoverageSegment[];
  path_points_enu: EnuCoordinate[];
  path_points_wgs84: GeoPoint[];
  estimated_path_length_m: number;
  estimated_search_time_s: number;
  number_of_search_lines: number;
  number_of_turns: number;
  validation: {
    valid: boolean;
    errors: string[];
    warnings: string[];
    planned_coverage_percent: number;
    covered_area_m2: number;
    missed_area_m2: number;
  };
  plan_fingerprint?: string | null;
  mission_fingerprint?: string | null;
  metadata: Record<string, unknown>;
}

export interface MissionExecution {
  mission_id: string;
  source: ExecutionSource;
  state: string;
  mission_fingerprint?: string | null;
  autopilot_version?: string | null;
  ardupilot_git_commit?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  current_mission_item: number;
  total_mission_items: number;
  waypoints_reached: number[];
  actual_trail: GeoPoint[];
  actual_path_length_m: number;
  actual_coverage_percent: number;
  missed_area_m2?: number | null;
  overlap_area_m2?: number | null;
  mean_deviation_m?: number | null;
  p95_deviation_m?: number | null;
  max_deviation_m?: number | null;
  rtl_completed: boolean;
  landing_detected: boolean;
  disarm_detected: boolean;
  errors: string[];
  metadata?: Record<string, unknown>;
}

export interface CameraCalibrationView {
  calibration_id: string;
  distortion_model: "PINHOLE_BROWN" | "FISHEYE";
  image_width: number;
  image_height: number;
  camera_matrix: [[number, number, number], [number, number, number], [number, number, number]];
  distortion_coefficients: number[];
  resolution: [number, number];
  sensor_mode: string;
  quality: string;
  evidence_class: EvidenceClass;
}

export interface CameraExtrinsicsView {
  extrinsics_id: string;
  mount_type: "FIXED" | "GIMBAL";
  translation_body_m: [number, number, number];
  quaternion_camera_to_body: [number, number, number, number];
  quality: string;
  evidence_class: EvidenceClass;
}

export interface VehicleGeometryProfile {
  profile_id: string;
  length_m: number | null;
  width_m: number | null;
  height_m: number | null;
  arm_span_m: number | null;
  rotor_radius_m: number | null;
  body_origin_offset_m: [number, number, number];
  autopilot_reference_offset_m: [number, number, number];
  camera_mount_position_m: [number, number, number];
  payload_mount_position_m: [number, number, number];
  evidence_class: EvidenceClass;
}

export interface DigitalTwinVehicle {
  vehicle_id: string;
  position_enu_m: [number, number, number];
  position_wgs84: GeoPoint;
  quaternion_body_to_enu: [number, number, number, number];
  velocity_enu_m_s: [number, number, number];
  telemetry_timestamp: string;
  evidence_class: EvidenceClass;
  stale: boolean;
}

export interface TargetObservationView {
  observation_id: string;
  target_id: string;
  vehicle_id: string;
  frame_id: string;
  capture_timestamp: string;
  chosen_pixel: [number, number];
  pixel_strategy: "BBOX_CENTER" | "BBOX_BOTTOM_CENTER";
  camera_ray: [number, number, number];
  body_ray: [number, number, number];
  world_ray_enu: [number, number, number];
  camera_origin_enu_m?: [number, number, number];
  target_enu_m: [number, number, number];
  range_m: number;
  detection_confidence: number;
  position_uncertainty_m: number;
  evidence_class: EvidenceClass;
}

export interface DigitalTwinSnapshot {
  mission_id: string;
  generated_at: string;
  execution_source: ExecutionSource;
  sequence: number;
  origin: GeoPoint | null;
  ground: { provider: "NONE" | "FLAT" | "LOCAL_DEM"; elevation_up_m: number; evidence_class: EvidenceClass };
  vehicle_geometry: VehicleGeometryProfile;
  camera: { calibration: CameraCalibrationView; extrinsics: CameraExtrinsicsView };
  vehicles: DigitalTwinVehicle[];
  observations: TargetObservationView[];
  launch_landing_zone: { width_m: number; length_m: number; physical_fit_verified: false };
  hardware_gate_h1: "PENDING";
}

export interface ReplaySample {
  timestamp: string;
  vehicle_id: string;
  position_wgs84: GeoPoint;
  position_enu_m: [number, number, number];
  quaternion_body_to_enu: [number, number, number, number];
  mode: string;
  mission_state: string;
  payload_state?: string;
  evidence_class: EvidenceClass;
}

export interface ReplayBundle {
  mission_id: string;
  start_time: string | null;
  end_time: string | null;
  samples: ReplaySample[];
  events: MissionEvent[];
  observations: TargetObservationView[];
  deterministic: true;
  source: ExecutionSource;
}
