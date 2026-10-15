import { useGcsStore } from "../stores/gcsStore";

export function HealthPanel() {
  const resilience = useGcsStore((state) => state.resilienceStatus);
  const twin = useGcsStore((state) => state.digitalTwin);
  const offline = useGcsStore((state) => state.offlineStatus);
  const vehicleState = useGcsStore((state) => state.vehicles);
  const vehicles = Object.values(vehicleState);
  const latestStale = twin?.vehicles.some((vehicle) => vehicle.stale) ?? true;
  const items = [
    ["Autopilot", vehicles.length ? "READY" : "UNKNOWN"], ["MAVLink", vehicles.some((vehicle) => vehicle.linkState === "CONNECTED") ? "READY" : "NOT READY"],
    ["GPS", vehicles.every((vehicle) => vehicle.gps_fix_type >= 3) && vehicles.length ? "READY" : "UNKNOWN"], ["EKF", "UNKNOWN"],
    ["Battery", vehicles.some((vehicle) => vehicle.battery_percent < 25) ? "DEGRADED" : vehicles.length ? "READY" : "UNKNOWN"],
    ["Camera", "PHYSICAL PENDING"], ["Detector", "PHYSICAL PENDING"], ["Clock sync", "SITL / UNKNOWN"],
    ["Pose", latestStale ? "STALE" : "READY"], ["Geolocation", twin ? "SYNTHETIC READY" : "NOT READY"],
    ["Payload", "SIMULATED"], ["Storage", "READY"], ["Database", offline ? "READY" : "UNKNOWN"],
    ["Fleet", resilience?.aggregate_health ?? "UNKNOWN"],
  ];
  return <section className="panel health-panel"><div className="panel-title"><h2>SYSTEM HEALTH</h2><strong className={`health-${(resilience?.aggregate_health ?? "NOT_READY").toLowerCase()}`}>{resilience?.aggregate_health ?? "NOT READY"}</strong></div>
    <div className="health-grid">{items.map(([label, state]) => <span key={label}><b>{label}</b><em className={state.includes("READY") && !state.includes("NOT") ? "ok" : state === "DEGRADED" || state === "STALE" ? "warning" : ""}>{state}</em></span>)}</div>
  </section>;
}
