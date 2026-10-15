import { useEffect, useState } from "react";
import { api, websocketUrl } from "../../services/api";
import { useGcsStore } from "../../stores/gcsStore";
import { FleetMap, appendFleetTrails, type FleetTrails } from "./FleetMap";
import { FleetScene3D } from "./FleetScene3D";
import type { DroneRole, FleetCameraFrame, FleetRuntimeStatus } from "./types";
import "./multiDrone.css";

const initial: FleetRuntimeStatus = {
  state: "IDLE",
  state_reason: "No active fleet session",
  session: null,
  vehicles: [],
  failed_vehicles: [],
  targets: [],
  assignments: [],
  deliveries: [],
  events: [],
  error: null,
  trails: {},
};

export const normalizeFleetStatus = (raw: Partial<FleetRuntimeStatus> | null | undefined): FleetRuntimeStatus => ({
  state: raw?.state ?? "IDLE",
  state_reason: raw?.state_reason ?? "",
  state_updated_at: raw?.state_updated_at,
  session: raw?.session ?? null,
  vehicles: Array.isArray(raw?.vehicles) ? raw.vehicles : [],
  failed_vehicles: Array.isArray(raw?.failed_vehicles) ? raw.failed_vehicles : [],
  targets: Array.isArray(raw?.targets) ? raw.targets : [],
  assignments: Array.isArray(raw?.assignments) ? raw.assignments : [],
  deliveries: Array.isArray(raw?.deliveries) ? raw.deliveries : [],
  events: Array.isArray(raw?.events) ? raw.events : [],
  error: raw?.error ?? null,
  trails: raw?.trails ?? {},
  partition: raw?.partition,
  coordination_state: raw?.coordination_state,
});

export const defaultStateExplanation = (state: string): string => {
  switch (state) {
    case "IDLE":
      return "No active fleet session";
    case "VALIDATING":
      return "Checking configuration";
    case "STARTING":
      return "Launching vehicles";
    case "WAITING_FOR_TELEMETRY":
      return "Waiting for vehicle connections";
    case "RUNNING":
      return "Fleet active";
    case "DEGRADED":
      return "One or more components need attention";
    case "RETURNING":
      return "Vehicles returning safely";
    case "STOPPING":
      return "Cleaning up simulation resources";
    case "STOPPED":
      return "Fleet stopped";
    case "ERROR":
      return "Startup or runtime failure";
    default:
      return "";
  }
};

export function MultiDronePage() {
  const mission = useGcsStore((state) => state.mission);
  const [status, setStatus] = useState<FleetRuntimeStatus>(initial);
  const [roles, setRoles] = useState<DroneRole[]>(["SEARCHER", "SERVICER"]);
  const [spacing, setSpacing] = useState(2);
  const [frames, setFrames] = useState<Record<string, FleetCameraFrame>>({});
  const [trails, setTrails] = useState<FleetTrails>({});
  const [message, setMessage] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = () =>
      api
        .multiDroneStatus()
        .then((value) => {
          if (!active) return;
          const safe = normalizeFleetStatus(value);
          setStatus(safe);
          setTrails((previous) =>
            safe.trails && Object.keys(safe.trails).length > 0
              ? safe.trails
              : appendFleetTrails(previous, safe.vehicles)
          );
          setNow(Date.now() / 1000);
        })
        .catch((error) => {
          if (active) setMessage(error instanceof Error ? error.message : String(error));
        });
    refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    setFrames({});
    setTrails({});
    if (!status.session?.session_id) return;
    const socket = new WebSocket(websocketUrl(status.session.session_id));
    socket.onmessage = (event) => {
      try {
        const envelope = JSON.parse(event.data) as {
          type: string;
          mission_id: string;
          vehicle_id: string;
          payload: FleetCameraFrame;
        };
        if (envelope?.type !== "camera_frame" || envelope?.mission_id !== status.session?.session_id) return;
        if (envelope.vehicle_id && envelope.payload) {
          setFrames((prior) => ({ ...prior, [envelope.vehicle_id]: envelope.payload }));
        }
      } catch {
        // Ignore unparseable frames
      }
    };
    socket.onerror = () => {
      // Prevent unhandled WebSocket error events from propagating
    };
    return () => socket.close();
  }, [status.session?.session_id]);

  const act = async (operation: () => Promise<unknown>) => {
    setMessage(null);
    try {
      await operation();
      setStatus(normalizeFleetStatus(await api.multiDroneStatus()));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };

  const start = async () => {
    if (!mission) {
      setMessage("Create and prepare a Mission in Simulation Lab first.");
      return;
    }
    setStarting(true);
    setMessage(null);
    try {
      const drones = roles.map((role, index) => ({
        vehicle_id: `drone_${String(index + 1).padStart(2, "0")}`,
        role,
        system_id: index + 1,
        instance_id: index,
        spawn_north_m: index * spacing,
        spawn_heading_deg: 90,
      }));
      const res = await api.startMultiDrone({ mission_id: mission.id, drones, spawn_spacing_m: spacing });
      setStatus(normalizeFleetStatus(res as FleetRuntimeStatus));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setStarting(false);
    }
  };

  const canStart = ["IDLE", "STOPPED", "ERROR"].includes(status.state) && Boolean(mission) && !starting;
  const canStop =
    ["STARTING", "WAITING_FOR_TELEMETRY", "RUNNING", "DEGRADED", "RETURNING"].includes(status.state) &&
    status.state !== "STOPPING";
  const canEditRoster = ["IDLE", "STOPPED", "ERROR"].includes(status.state) && !starting;

  const vehicles = status.vehicles ?? [];
  const targets = status.targets ?? [];
  const assignments = status.assignments ?? [];
  const deliveries = status.deliveries ?? [];
  const events = status.events ?? [];
  const failedVehicles = status.failed_vehicles ?? [];

  return (
    <main className="fleet-page">
      <header className="fleet-heading">
        <div>
          <h1>Multi-Drone Simulation</h1>
          <p>Independent ArduCopter + Gazebo vehicles · authoritative lifecycle state machine</p>
        </div>
        <div className="state-badge">
          <span className={`state-pill state-${status.state}`}>{status.state.replace(/_/g, " ")}</span>
          <span className="state-explanation">{status.state_reason || defaultStateExplanation(status.state)}</span>
        </div>
      </header>

      <section className="fleet-control panel">
        <div>
          <strong>Fleet roster</strong>
          <small>
            {" "}
            Prepare this mission in Simulation Lab before starting. Routes are partitioned; no coordinated delivery is active.
          </small>
        </div>
        <label>
          Spawn spacing{" "}
          <input
            aria-label="Spawn spacing"
            type="number"
            min="1.5"
            max="20"
            step="0.5"
            disabled={!canEditRoster}
            value={spacing}
            onChange={(event) => setSpacing(Number(event.target.value))}
          />{" "}
          m
        </label>
        <button
          disabled={!canEditRoster || roles.length >= 4}
          onClick={() => setRoles((prior) => [...prior, "SEARCHER"])}
        >
          Add drone
        </button>
        <button
          disabled={!canEditRoster || roles.length <= 2}
          onClick={() => setRoles((prior) => prior.slice(0, -1))}
        >
          Remove drone
        </button>
        {roles.map((role, index) => (
          <label key={index}>
            drone_{String(index + 1).padStart(2, "0")}{" "}
            <select
              aria-label={`drone_${String(index + 1).padStart(2, "0")} role`}
              disabled={!canEditRoster}
              value={role}
              onChange={(event) =>
                setRoles((prior) => prior.map((item, i) => (i === index ? (event.target.value as DroneRole) : item)))
              }
            >
              <option value="SEARCHER">SEARCHER</option>
              <option value="SERVICER">SERVICER</option>
            </select>
          </label>
        ))}
        <button disabled={!canStart} onClick={start}>
          {starting ? "Starting fleet..." : "Start fleet"}
        </button>
        <button disabled={!canStop} onClick={() => void act(api.stopMultiDrone)}>
          Safe fleet return / stop
        </button>
      </section>

      {(message || status.error) && (
        <div role="alert" className="fleet-error">
          <strong>Notice: </strong>
          {message ?? status.error}
        </div>
      )}

      <section className="fleet-summary panel">
        <strong>Session {status.session?.session_id ? String(status.session.session_id).slice(0, 8) : "none"}</strong>
        <span>Confirmed canonical survivors: {targets.length}</span>
        <span>Service assignments: {assignments.length}</span>
        <span>Verified deliveries: {deliveries.filter((item: any) => item?.state === "VERIFIED" || item?.verified).length}</span>
        <small>Phase 2B authoritative survivor registry, exclusive service coordinator, and autonomous delivery verification active.</small>
      </section>

      <div className="fleet-visuals">
        <section className="panel">
          <h2>Fleet map · real GPS trails</h2>
          <FleetMap vehicles={vehicles} trails={trails} />
        </section>
        <section className="panel">
          <h2>Fleet 3D · real telemetry</h2>
          <FleetScene3D vehicles={vehicles} />
        </section>
      </div>

      <div className="fleet-coordination-grid">
        <section className="panel survivor-registry-panel" aria-label="Shared Survivor Registry">
          <header className="panel-header">
            <h2>Shared Survivor Registry</h2>
            <span className="badge-count">{targets.length} Confirmed</span>
          </header>
          {targets.length === 0 ? (
            <p className="empty-panel-text">No confirmed survivors registered yet. Observations remain internal candidates until temporally confirmed.</p>
          ) : (
            <div className="registry-table-container">
              <table className="coordination-table">
                <thead>
                  <tr>
                    <th>Canonical ID</th>
                    <th>Priority / source</th>
                    <th>Status</th>
                    <th>Last Seen</th>
                    <th>Drones</th>
                    <th>Position / Uncertainty</th>
                    <th>Obs</th>
                    <th>Assignment</th>
                  </tr>
                </thead>
                <tbody>
                  {targets.map((target) => {
                    const posStr =
                      target.latitude != null && target.longitude != null
                        ? `${target.latitude.toFixed(5)}, ${target.longitude.toFixed(5)} (±${(target.uncertainty_m ?? 1).toFixed(1)}m)`
                        : target.east_m != null && target.north_m != null
                        ? `[E:${target.east_m.toFixed(1)}, N:${target.north_m.toFixed(1)}] (±${(target.uncertainty_m ?? 1).toFixed(1)}m)`
                        : "Unavailable";
                    const lastObsStr = target.last_seen_at
                      ? new Date(target.last_seen_at).toLocaleTimeString()
                      : "—";
                    const isConfirmed = target.confirmation_status === "CONFIRMED";
                    return (
                      <tr key={target.canonical_id}>
                        <td className="mono font-bold text-accent">{target.canonical_id}</td>
                        <td>
                          <select
                            aria-label={`Priority for ${target.canonical_id}`}
                            value={target.priority ?? "NORMAL"}
                            disabled={!(["RUNNING", "DEGRADED"].includes(status.state))}
                            onChange={(event) => void act(() => api.setMultiDroneSurvivorPriority(
                              target.canonical_id,
                              event.target.value as "CRITICAL" | "HIGH" | "NORMAL" | "LOW",
                            ))}
                          >
                            <option value="CRITICAL">CRITICAL</option>
                            <option value="HIGH">HIGH</option>
                            <option value="NORMAL">NORMAL</option>
                            <option value="LOW">LOW</option>
                          </select>
                          <small className="priority-source">{target.priority_source ?? "DEFAULT"}</small>
                        </td>
                        <td>
                          <span className={`status-badge ${isConfirmed ? "badge-confirmed" : "badge-candidate"}`}>
                            {target.confirmation_status}
                          </span>
                          {target.delivery_status && <small className="priority-source">Delivery: {target.delivery_status}</small>}
                        </td>
                        <td>{lastObsStr}</td>
                        <td>
                          <div className="drone-tags">
                            {target.source_drone_ids.map((did) => (
                              <span key={did} className="drone-mini-tag">{did}</span>
                            ))}
                          </div>
                        </td>
                        <td className="mono">{posStr}</td>
                        <td className="mono">{target.observation_count}</td>
                        <td>
                          <span className={`assignment-tag tag-${String(target.assignment_state || "UNASSIGNED").toLowerCase()}`}>
                            {target.assigned_servicer_id
                              ? `${target.assignment_state} (${target.assigned_servicer_id})`
                              : target.assignment_state || "UNASSIGNED"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel service-queue-panel" aria-label="Service Queue and Assignments">
          <header className="panel-header">
            <h2>Service Queue & Assignments</h2>
            <span className="badge-count">{assignments.length} Jobs</span>
          </header>
          {assignments.length === 0 ? (
            <p className="empty-panel-text">No service jobs queued or assigned.</p>
          ) : (
            <div className="assignments-table-container">
              <table className="coordination-table">
                <thead>
                  <tr>
                    <th>Job ID</th>
                    <th>Queue / priority</th>
                    <th>Survivor ID</th>
                    <th>Stage</th>
                    <th>Servicer</th>
                    <th>Payload Release</th>
                    <th>Verification</th>
                    <th>Updated</th>
                    <th>Reason / Notes</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {assignments.map((job) => {
                    const timeStr = job.updated_at
                      ? new Date(job.updated_at).toLocaleTimeString()
                      : job.created_at
                      ? new Date(job.created_at).toLocaleTimeString()
                      : "—";
                    const isAssigned = job.state === "ASSIGNED";
                    const stateClass = `badge-${String(job.state).toLowerCase()}`;
                    return (
                      <tr key={job.assignment_id}>
                        <td className="mono">{String(job.assignment_id).slice(0, 8)}</td>
                        <td className="mono">{job.queue_position ? `#${job.queue_position} · ` : ""}{job.priority ?? "NORMAL"}</td>
                        <td className="mono font-bold text-accent">{job.canonical_survivor_id}</td>
                        <td>
                          <span className={`status-badge ${stateClass}`}>
                            {job.state.replace(/_/g, " ")}
                          </span>
                        </td>
                        <td>
                          {job.servicer_id ? (
                            <span className="drone-mini-tag servicer-tag">{job.servicer_id}</span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td>
                          {job.payload_released ? (
                            <span className="drone-mini-tag badge-released" title={job.release_id || "Simulated release"}>
                              RELEASED
                            </span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td>
                          {job.delivery_verified ? (
                            <span className="drone-mini-tag badge-delivered">VERIFIED</span>
                          ) : job.state === "VERIFYING" ? (
                            <span className="drone-mini-tag badge-verifying">VERIFYING</span>
                          ) : job.failure_reason ? (
                            <span className="drone-mini-tag badge-failed" title={job.failure_reason}>FAILED</span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td>{timeStr}</td>
                        <td className="reason-cell" title={job.reason || job.failure_reason || ""}>
                          {job.failure_reason || job.reason || "—"}
                        </td>
                        <td>
                          {isAssigned && (
                            <button
                              className="btn-ack"
                              onClick={() => void act(() => api.acknowledgeMultiDroneAssignment(job.assignment_id))}
                            >
                              ACK
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <section className="fleet-cards" aria-label="Fleet vehicles">
        {vehicles.map((vehicle) => {
          const frame = frames[vehicle.vehicle_id];
          const live = Boolean(
            frame &&
              typeof frame.source_ts === "number" &&
              now - frame.source_ts < 3 &&
              vehicle.camera_status === "LIVE"
          );
          const t = vehicle.telemetry;
          const altStr =
            typeof t?.relative_altitude_m === "number" && Number.isFinite(t.relative_altitude_m)
              ? `${t.relative_altitude_m.toFixed(1)} m`
              : "—";
          const spdStr =
            typeof t?.ground_speed_m_s === "number" && Number.isFinite(t.ground_speed_m_s)
              ? `${t.ground_speed_m_s.toFixed(1)} m/s`
              : "—";
          const gpsStr =
            typeof t?.latitude === "number" &&
            typeof t?.longitude === "number" &&
            Number.isFinite(t.latitude) &&
            Number.isFinite(t.longitude)
              ? `${t.latitude.toFixed(6)}, ${t.longitude.toFixed(6)}`
              : "—";
          const progStr =
            typeof vehicle.progress_percent === "number" && Number.isFinite(vehicle.progress_percent)
              ? `${vehicle.progress_percent.toFixed(1)}%`
              : "0.0%";
          const detectionCount = live && Array.isArray(frame?.detections) ? frame.detections.length : 0;
          const isFailed = vehicle.state === "FAILED";
          const isDegraded = failedVehicles.includes(vehicle.vehicle_id) && !isFailed;
          const canReturnVehicle =
            ["READY", "SEARCHING", "RETURNING"].includes(String(vehicle.state)) &&
            status.state !== "STOPPING" &&
            status.state !== "STOPPED";

          return (
            <article
              className={`panel fleet-card ${isFailed ? "is-failed" : isDegraded ? "is-degraded" : ""}`}
              key={vehicle.vehicle_id}
            >
              <header>
                <h2>{vehicle.vehicle_id}</h2>
                <div className="fleet-card-badges">
                  <span className={`drone-status-tag state-${vehicle.state}`}>
                    {String(vehicle.state).replace(/_/g, " ")}
                  </span>
                  <b className={vehicle.role === "SEARCHER" ? "searcher" : "servicer"}>{vehicle.role}</b>
                </div>
              </header>

              {(isFailed || isDegraded) && (
                <div className="fleet-card-alert">
                  ⚠️ {vehicle.error || (isDegraded ? "Subsystem or telemetry stale" : "Vehicle failure reported")}
                </div>
              )}

              <div className="fleet-metrics">
                <span>Link {vehicle.connected ? "CONNECTED" : "STALE"}</span>
                <span>Mode {t?.flight_mode ?? "—"}</span>
                <span>{t?.armed ? "ARMED" : "DISARMED"}</span>
                <span>Altitude {altStr}</span>
                <span>Speed {spdStr}</span>
                <span>GPS {gpsStr}</span>
                <span>Search {progStr}</span>
                <span>
                  Item {vehicle.current_mission_item ?? 0}/{vehicle.total_mission_items ?? 0}
                </span>
                <span>SYSID {vehicle.system_id}</span>
              </div>
              <small>
                Assigned search segments {vehicle.sector_ids?.length ?? 0} · planned altitude{" "}
                {vehicle.route_configuration?.search_altitude_m ?? "—"} m · search speed{" "}
                {vehicle.route_configuration?.search_speed_m_s ?? "—"} m/s
              </small>
              <div className="fleet-camera">
                <strong>Camera {live ? "LIVE" : vehicle.camera_status}</strong>
                {live && (
                  <img
                    src={`data:image/jpeg;base64,${frame.image_b64}`}
                    alt={`${vehicle.vehicle_id} live Gazebo camera`}
                  />
                )}
                {!live && (
                  <p>
                    {vehicle.camera_frame_count
                      ? `Stream ended or stale after ${vehicle.camera_frame_count} frames`
                      : "No frames received for this vehicle"}
                  </p>
                )}
                <small>
                  Frames {vehicle.camera_frame_count} · decoded {vehicle.camera_decoded_count} · inferred{" "}
                  {vehicle.camera_inference_count} · model {vehicle.camera_model_status ?? "WAITING"} · detections{" "}
                  {detectionCount}
                </small>
              </div>
              <div className="fleet-card-footer">
                <small>{vehicle.last_event ?? "Waiting for vehicle events"}</small>
                <button
                  disabled={!canReturnVehicle || vehicle.state === "RETURNING"}
                  onClick={() => void act(() => api.returnMultiDroneVehicle(vehicle.vehicle_id))}
                >
                  Return this drone
                </button>
              </div>
              {vehicle.role === "SEARCHER" ? (
                <small>Delivery forbidden by backend role gate.</small>
              ) : (() => {
                const activeJob = assignments.find(
                  (a) => a.servicer_id === vehicle.vehicle_id &&
                  ["ASSIGNED", "ACKNOWLEDGED", "APPROACHING", "HOLDING_FOR_RELEASE", "RELEASED", "VERIFYING", "DELIVERED", "RESUMING_SEARCH"].includes(a.state)
                );
                if (activeJob) {
                  return (
                    <small>
                      Servicing {activeJob.canonical_survivor_id} · Stage: <b>{activeJob.state.replace(/_/g, " ")}</b>
                      {activeJob.payload_released ? " · Payload released" : ""}
                      {activeJob.delivery_verified ? " · Verified" : ""}
                    </small>
                  );
                }
                return <small>SERVICER ready for autonomous dispatch · Search active.</small>;
              })()}
            </article>
          );
        })}
      </section>

      <section className="panel fleet-timeline">
        <h2>State Transition Timeline</h2>
        {events.length === 0 ? (
          <small>No lifecycle events recorded yet.</small>
        ) : (
          <div className="timeline-list">
            {[...events].reverse().map((ev, idx) => {
              const timeStr = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : "--:--:--";
              return (
                <div key={idx} className={`timeline-item sev-${ev.severity || "INFO"}`}>
                  <span className="timeline-time">{timeStr}</span>
                  {ev.vehicle_id && <span className="timeline-vehicle">{ev.vehicle_id}</span>}
                  <span className="timeline-type">{ev.type}</span>
                  <span className="timeline-msg">{ev.message}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
