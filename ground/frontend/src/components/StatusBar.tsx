import { useGcsStore } from "../stores/gcsStore";
import { CopyButton } from "./CopyButton";

function elapsed(seconds = 0): string {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const remainder = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remainder}`;
}

export function StatusBar() {
  const { mode, offlineStatus, backendState, websocketState, mission, vehicles, executionSource, rescueState, hardwareGateH1, resilienceStatus, competitionMode } = useGcsStore();
  const vehicleValues = Object.values(vehicles);
  const linked = vehicleValues.filter((vehicle) => vehicle.linkState === "CONNECTED").length;
  const criticalAlerts = resilienceStatus?.safety_decision && ["CRITICAL", "EMERGENCY"].includes(resilienceStatus.safety_decision.severity) ? 1 : 0;
  const fleetState = useGcsStore((state) => state.fleetStatus?.state);
  return (
    <header className="status-bar">
      <div className="brand"><span className="brand-mark">RS</span><span>RESCUESWARM</span></div>
      <div className="header-mission" title={mission?.id ?? "No active mission"}><small>MISSION</small><b>{mission?.name ?? "NO ACTIVE MISSION"}</b>{mission?.id && <><code>{mission.id.slice(0, 8)}</code><CopyButton value={mission.id} label="Copy mission ID" /></>}</div>
      <div className="status-items" aria-label="System status">
        <span className="mode-label">{mode}</span>
        <span className={resilienceStatus?.aggregate_health === "EMERGENCY" ? "danger" : resilienceStatus?.aggregate_health === "READY" ? "ok" : "warning"} title="Aggregated autonomy and vehicle health">SAFETY {resilienceStatus?.aggregate_health ?? "NOT READY"}</span>
        <span title="Fleet mission state">FLEET {mode === "MULTIDRONE" ? "SIMULATION" : fleetState ?? mission?.state ?? "NONE"}</span>
        <span title="Fresh vehicle links">LINK {linked}/{vehicleValues.length}</span>
        <span className={offlineStatus?.status === "READY" ? "ok" : "danger"} title="No public internet dependency">OFFLINE LOCAL {offlineStatus?.status ?? "CHECKING"}</span>
        <span title={`Backend ${backendState}; WebSocket ${websocketState}`} className={websocketState === "CONNECTED" ? "" : "warning"}>DATA {websocketState === "CONNECTED" ? "LIVE" : websocketState}</span>
        {competitionMode && <span className="competition-badge">COMPETITION MODE</span>}
        {executionSource !== "REAL" && <span className="simulation-badge" title={executionSource === "SITL" ? "ArduCopter software-in-the-loop evidence" : "Non-physical evidence"}>SIMULATION</span>}
        {hardwareGateH1?.state === "PENDING" && <span className="truth-badge" title="Physical prototype validation has not been performed">H1 PENDING</span>}
        {criticalAlerts > 0 && <span className="critical-count" role="status">▲ {criticalAlerts} CRITICAL</span>}
        <time aria-label="Mission elapsed time">{elapsed(mission?.elapsed_seconds)}</time>
      </div>
      <span className="sr-only">Rescue state {rescueState}. Hardware Gate H1 {hardwareGateH1?.state ?? "PENDING"}. Backend {backendState}.</span>
    </header>
  );
}
