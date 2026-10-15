import { EventFeed } from "../components/EventFeed";
import { MissionSummary } from "../components/MissionSummary";
import { TargetList } from "../components/TargetList";
import { VehiclePanel } from "../components/VehiclePanel";
import { FleetPanel } from "../components/FleetPanel";
import { HealthPanel } from "../components/HealthPanel";
import { CameraFeedPanel } from "../components/CameraFeedPanel";
import { MissionSimulation2D } from "../components/MissionSimulation2D";
import { MissionSimulation3D } from "../components/MissionSimulation3D";
import { SurvivorLocationAccuracy, type SimulationTruthSurvivor } from "../components/SurvivorLocationAccuracy";
import { PanelDisclosure } from "../components/PanelDisclosure";
import { FlightParametersPanel } from "../components/FlightParametersPanel";
import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
type SimButtonState = "READY" | "STARTING" | "CONNECTING" | "STARTED" | "RUNNING" | "FAILED" | "STOPPED";
export function MissionPage() {
  const mission = useGcsStore((state) => state.mission);
  const safety = useGcsStore((state) => state.resilienceStatus);
  const setError = useGcsStore((state) => state.setError);
  const railOpen = useGcsStore((state) => state.missionRailOpen);
  const setRailOpen = useGcsStore((state) => state.setMissionRailOpen);
  const panels = useGcsStore((state) => state.panelVisibility);
  const setPanel = useGcsStore((state) => state.setPanelVisibility);
  const targets = useGcsStore((state) => state.targets);
  const executionSource = useGcsStore((state) => state.executionSource);
  const vehicles = useGcsStore((state) => state.vehicles);
  const fleetState = useGcsStore((state) => state.fleetStatus?.state);
  const rescueState = useGcsStore((state) => state.rescueState);
  const viewMode = useGcsStore((state) => state.viewMode);
  const setViewMode = useGcsStore((state) => state.setViewMode);
  // Remove unused:
  // const setVehicles = useGcsStore((state) => state.setInitialData);

  const serviceStates = ["TARGET_CANDIDATE", "TARGET_CONFIRMED", "SEARCH_SUSPENDING", "APPROACH_PLANNING", "GUIDED_APPROACH", "REACQUIRING", "PRECISION_ALIGNING", "DROP_READY", "DELIVERING", "DELIVERY_VERIFYING"];
  const simIsLive = Object.values(vehicles).some((v) => v.armed && v.linkState !== "STALE" && v.linkState !== "DISCONNECTED");
  const terminalMissionState = mission?.state === "COMPLETED" ? "MISSION COMPLETE" : 
                               (!simIsLive && ["ABORTED", "FAILED"].includes(mission?.state ?? "")) ? mission!.state : null;
  const autoSearchEvidence = simIsLive && Object.values(vehicles).some((vehicle) => vehicle.flight_mode === "AUTO");
  const activeFleetState = fleetState && fleetState !== "NOT_PLANNED" ? fleetState : null;
  const missionState = terminalMissionState ?? (serviceStates.includes(rescueState) ? "TARGET SERVICE" : rescueState === "LANDING" ? "LANDING" : autoSearchEvidence ? "SEARCHING" : simIsLive ? "RUNNING" : activeFleetState ?? mission?.state ?? "SIMULATION READY");
  const activeVehicles = Object.values(vehicles).filter((vehicle) => vehicle.linkState === "CONNECTED").length;
  const confirmedTargets = Object.values(targets).filter((target) => target.state !== "REJECTED" && target.state !== "CANDIDATE").length;
  const [evalMode, setEvalMode] = useState(false);
  const [observerData, setObserverData] = useState<any>(null);

  // Real simulation button state — NO fake animation
  const [simBtn, setSimBtn] = useState<SimButtonState>("READY");
  const [simMessage, setSimMessage] = useState("");
  const [simFailReason, setSimFailReason] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Read prepared scenario from localStorage
  const scenarioRaw = localStorage.getItem("rescueswarm.simulationScenario");
  const scenario: any = scenarioRaw ? (() => { try { return JSON.parse(scenarioRaw); } catch { return null; } })() : null;
  const scenarioSurvivorCount: number = scenario?.survivors?.length ?? 0;
  const scenarioPolygonCount: number = scenario?.polygon?.length ?? 0;
  const scenarioStart = scenario?.startPosition as { lng: number; lat: number } | null;

  const hasScenario = !!mission || !!scenarioRaw;
  const canStartSim = hasScenario && (simBtn === "READY" || simBtn === "STOPPED" || simBtn === "FAILED");


  // Log when MissionPage mounts
  useEffect(() => {
    console.log("[SIMULATION] MissionPage mounted | mission:", mission?.id ?? "none", "| polygon:", scenarioPolygonCount, "| survivors:", scenarioSurvivorCount, "| start:", JSON.stringify(scenarioStart));
  }, []);

  // Detect real telemetry arriving → switch from CONNECTING to RUNNING
  useEffect(() => {
    if (simBtn !== "CONNECTING") return;
    const vehicleIds = Object.keys(vehicles);
    if (vehicleIds.length > 0) {
      const v = vehicles[vehicleIds[0]];
      if (v && v.latitude && v.longitude) {
        console.log("[SIMULATION] telemetry received | lat:", v.latitude, "lon:", v.longitude, "alt:", v.relative_altitude_m);
        setSimBtn("RUNNING");
        setSimMessage(`RUNNING — drone at ${v.latitude.toFixed(6)}, ${v.longitude.toFixed(6)}`);
      }
    }
  }, [vehicles, simBtn]);

  // Log when position changes during RUNNING
  const prevVehicleLatRef = useRef<number | null>(null);
  useEffect(() => {
    if (simBtn !== "RUNNING") return;
    const vids = Object.keys(vehicles);
    if (vids.length === 0) return;
    const v = vehicles[vids[0]];
    if (v && v.latitude !== prevVehicleLatRef.current) {
      console.log("[SIMULATION] lat/lon changed | lat:", v.latitude, "lon:", v.longitude, "alt:", v.relative_altitude_m, "armed:", v.armed, "mode:", v.flight_mode);
      prevVehicleLatRef.current = v.latitude ?? null;
      setSimMessage(`RUNNING — ${v.flight_mode} | armed:${v.armed} | lat:${v.latitude?.toFixed(5)}, lon:${v.longitude?.toFixed(5)}, alt:${v.relative_altitude_m?.toFixed(1)}m`);
    }
  }, [vehicles, simBtn]);



  useEffect(() => {
    if (!evalMode || executionSource === "REAL") return;
    const fetchTruth = async () => {
      try {
        const [truth, metrics] = await Promise.all([api.simulationObserverTruth(), api.simulationPerceptionObserver()]);
        setObserverData({ truth, metrics });
      } catch (e) { console.error("[SIMULATION] observer truth fetch failed:", e); }
    };
    fetchTruth();
    const timer = setInterval(fetchTruth, 5000);
    return () => clearInterval(timer);
  }, [evalMode, executionSource, mission?.id]);

  // Cleanup poll on unmount
  useEffect(() => { return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);

  const prevVehicleLatRef2 = useRef<number | null>(null); // duplicate ref guard

  /**
   * START SIMULATION: calls real api.executeSitl() only.
   * NO fake animation. NO setInterval movement. NO synthetic drone positions.
   * Real drone coordinates come exclusively from MAVLink -> WebSocket -> Zustand.
   */
  const startMissionFlight = async () => {
    if (!canStartSim) return;
    if (!mission) {
      setSimBtn("FAILED");
      setSimFailReason("No mission record found. Complete setup wizard first.");
      return;
    }
    console.log("[SIMULATION] start requested | mission:", mission.id, "| state:", mission.state);
    setSimBtn("STARTING");
    setSimMessage("STARTING — sending execute command to backend…");
    setSimFailReason("");
    try {
      const result = await api.executeSitl(mission.id);
      console.log("[SIMULATION] SITL execute response:", JSON.stringify(result));
      setSimBtn("CONNECTING");
      setSimMessage("CONNECTING — waiting for MAVLink telemetry via WebSocket…");
      setError(null);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      console.error("[SIMULATION] start failed:", reason);
      setSimBtn("FAILED");
      setSimFailReason(reason);
      setSimMessage("FAILED: " + reason);
    }
  };

  const stopSimulation = async () => {
    try { await api.abortSitl(mission?.id ?? ""); } catch { /* ignore */ }
    setSimBtn("STOPPED");
    setSimMessage("STOPPED — mission aborted.");
  };

  const action = async (name: "SAFETY_ABORT" | "EMERGENCY_RECALL") => {
    if (!mission) return;
    const msg = name === "EMERGENCY_RECALL" ? "Recall every active SITL vehicle?" : "Abort the active SITL mission?";
    if (!window.confirm(msg)) return;
    try { await api.operatorAction(mission.id, name, mission.state); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : `${name} FAILED`); }
  };

  const btnLabel: Record<SimButtonState, string> = {
    READY: "▶ START SIMULATION", STARTING: "⏳ STARTING…", CONNECTING: "📡 CONNECTING…",
    STARTED: "✅ RUNNING", RUNNING: "✅ RUNNING", FAILED: "⚠ RETRY START", STOPPED: "▶ RESTART",
  };



  return (
    <main className={`mission-page ${railOpen ? "" : "rail-collapsed"}`}>
      {safety?.safety_decision && safety.safety_decision.severity !== "INFO" && (
        <div className={`failsafe-banner severity-${safety.safety_decision.severity.toLowerCase()}`}>
          <b>{safety.safety_decision.severity}: {safety.safety_decision.reason}</b>
          <span>OWNER {safety.safety_decision.authoritative_layer} · {safety.safety_decision.recommended_action}</span>
        </div>
      )}
      <section className={`mission-command-bar mission-${missionState.toLowerCase().replaceAll("_", "-").replaceAll(" ", "-")}`} aria-label="Current mission state">
        <div><span>CURRENT MISSION STATE</span><strong>{missionState.replaceAll("_", " ")}</strong></div>
        <dl>
          <div><dt>Coverage</dt><dd>{(mission?.coverage_percent?.toFixed(1) ?? "—") + "%"}</dd></div>
          <div><dt>Active fleet</dt><dd>{activeVehicles}/{Object.keys(vehicles).length}</dd></div>
          <div><dt>Survivors (auto)</dt><dd>{confirmedTargets}</dd></div>
          <div><dt>Search Alt/Spd</dt><dd>{(mission?.flight_parameters?.search_altitude_m ?? 15.0).toFixed(0)}m / {(mission?.flight_parameters?.search_speed_m_s ?? 4.0).toFixed(0)}m/s</dd></div>
          <div><dt>Deliv Alt/Standoff</dt><dd>{(mission?.flight_parameters?.delivery_altitude_m ?? 4.0).toFixed(0)}m / {(mission?.flight_parameters?.safe_drop_offset_m ?? 2.5).toFixed(1)}m</dd></div>
          <div><dt>Scenario</dt><dd>{scenarioSurvivorCount} survivor(s) · {scenarioPolygonCount}pt polygon</dd></div>
        </dl>
        <div className="competition-actions">
          {(simBtn === "READY" || simBtn === "STOPPED" || simBtn === "FAILED") && (
            <button id="btn-start-simulation" className="primary success" style={{ marginRight: "15px" }}
              disabled={!canStartSim} onClick={startMissionFlight}>
              {btnLabel[simBtn]}
            </button>
          )}
          {(simBtn === "STARTING" || simBtn === "CONNECTING") && (
            <button className="primary" style={{ marginRight: "15px", opacity: 0.7 }} disabled>
              {btnLabel[simBtn]}
            </button>
          )}
          {simBtn === "RUNNING" && (
            <button className="abort-button" style={{ marginRight: "15px" }} onClick={stopSimulation}>
              ⏹ STOP SIMULATION
            </button>
          )}
          <label className="eval-toggle">
            <input type="checkbox" checked={evalMode} onChange={(e) => setEvalMode(e.target.checked)} />
            {" "}<b>{evalMode ? "EVALUATION MODE" : "OPERATOR MODE"}</b>
          </label>
          <button className="abort-button" disabled={!mission || !["RUNNING", "RETURNING"].includes(mission.state)} onClick={() => action("SAFETY_ABORT")}>SAFETY ABORT</button>
          <button className="recall-button" disabled={!mission || !["RUNNING", "RETURNING"].includes(mission.state)} onClick={() => action("EMERGENCY_RECALL")}>EMERGENCY RECALL</button>
        </div>
      </section>

      {simMessage && (
        <div style={{ background: simBtn === "FAILED" ? "#2d1111" : "#0f2518", borderBottom: "1px solid #267c63", padding: "8px 16px", fontSize: "0.85rem", color: simBtn === "FAILED" ? "#f87171" : "#54e7b1", display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ flex: 1 }}>{simMessage}</span>
          {simBtn === "FAILED" && simFailReason && <span style={{ fontSize: "0.78rem", color: "#fca5a5" }}>Cause: {simFailReason}</span>}
        </div>
      )}

      {scenario && simBtn !== "RUNNING" && (
        <div style={{ background: "#0d1f35", borderBottom: "1px solid #1e3a5f", padding: "6px 16px", fontSize: "0.8rem", color: "#93c5fd", display: "flex", gap: "20px", flexWrap: "wrap" }}>
          <span>📍 SCENARIO: {scenarioPolygonCount}-vertex search polygon</span>
          <span>🧑 {scenarioSurvivorCount} scenario survivor(s) placed</span>
          {scenarioStart && <span>🚁 Drone start: {scenarioStart.lat.toFixed(5)}, {scenarioStart.lng.toFixed(5)}</span>}
          <span style={{ marginLeft: "auto", color: "#60a5fa" }}>Scenario markers on map · survivors are SCENARIO TRUTH only — NOT autonomous targets</span>
        </div>
      )}

      <div className="mission-layout-tools"><button onClick={() => setRailOpen(!railOpen)} aria-expanded={railOpen}>{railOpen ? "HIDE DETAILS" : "SHOW FLEET & TARGETS"}</button></div>

      {evalMode && observerData && (
        <aside className="evaluation-panel" style={{ background: "#1e293b", padding: "10px", color: "#fff", fontSize: "0.85rem" }}>
          <SurvivorLocationAccuracy
            runId={(observerData.truth?.run_id as string | null) ?? null}
            truth={(observerData.truth?.ground_truth?.survivors ?? []) as SimulationTruthSurvivor[]}
            targets={Object.values(targets)}
            simulationOnly={executionSource !== "REAL"}
          />
          <h3>SIMULATION PERCEPTION EVIDENCE</h3>
          <dl style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px" }}>
            <dt>Visible frames</dt><dd>{observerData?.metrics?.summary?.truth_visible_person_frames ?? "—"}</dd>
            <dt>Best error</dt><dd>{observerData?.metrics?.summary?.best_geolocation_error_m?.toFixed?.(2) ?? "—"} m</dd>
            <dt>Proof source</dt><dd>{observerData?.metrics?.prediction_vs_truth?.proof_source ?? "—"}</dd>
          </dl>
          {["MISSION COMPLETE", "COMPLETED"].includes(missionState ?? "") && (
            <div style={{ marginTop: "10px", borderTop: "1px solid #334155", paddingTop: "10px" }}>
              <h4>POST-MISSION RESULTS</h4>
              <dl style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px" }}>
                <dt>Search Area</dt><dd>{mission?.boundary?.area_hectares?.toFixed(2) ?? 0} ha</dd>
                <dt>Missed Detections</dt><dd>{observerData?.metrics?.prediction_vs_truth?.missed_detections ?? 0}</dd>
                <dt>False Detections</dt><dd>{observerData?.metrics?.prediction_vs_truth?.false_detections ?? 0}</dd>
              </dl>
            </div>
          )}
        </aside>
      )}

      <div className="map-region">
        <div
          className="simulation-view-switch"
          style={{
            position: "absolute",
            top: "10px",
            right: "10px",
            zIndex: 35,
            display: "flex",
            gap: "4px",
            background: "rgba(10, 25, 22, 0.9)",
            border: "1px solid rgba(84, 231, 177, 0.35)",
            borderRadius: "6px",
            padding: "4px",
            boxShadow: "0 4px 16px rgba(0, 0, 0, 0.4)",
            backdropFilter: "blur(8px)",
          }}
        >
          <button
            id="btn-view-2d"
            className={`view-toggle-btn ${viewMode === "2D" ? "active" : ""}`}
            style={{
              background: viewMode === "2D" ? "#10b981" : "transparent",
              color: viewMode === "2D" ? "#022c22" : "#a7f3d0",
              border: "none",
              borderRadius: "4px",
              padding: "4px 12px",
              fontSize: "0.75rem",
              fontWeight: 700,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
            onClick={() => setViewMode("2D")}
          >
            🗺️ 2D MAP
          </button>
          <button
            id="btn-view-3d"
            className={`view-toggle-btn ${viewMode === "3D" ? "active" : ""}`}
            style={{
              background: viewMode === "3D" ? "#10b981" : "transparent",
              color: viewMode === "3D" ? "#022c22" : "#a7f3d0",
              border: "none",
              borderRadius: "4px",
              padding: "4px 12px",
              fontSize: "0.75rem",
              fontWeight: 700,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
            onClick={() => setViewMode("3D")}
          >
            🛸 3D VIEW
          </button>
          <button
            id="btn-view-split"
            className={`view-toggle-btn ${viewMode === "SPLIT" ? "active" : ""}`}
            style={{
              background: viewMode === "SPLIT" ? "#10b981" : "transparent",
              color: viewMode === "SPLIT" ? "#022c22" : "#a7f3d0",
              border: "none",
              borderRadius: "4px",
              padding: "4px 12px",
              fontSize: "0.75rem",
              fontWeight: 700,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
            onClick={() => setViewMode("SPLIT")}
          >
            ◫ SPLIT
          </button>
        </div>

        {viewMode === "2D" && <MissionSimulation2D scenario={scenario} />}
        {viewMode === "3D" && <MissionSimulation3D scenario={scenario} />}
        {viewMode === "SPLIT" && (
          <div className="split-simulation-container">
            <div className="split-view-pane">
              <span className="split-pane-label">2D MAP</span>
              <MissionSimulation2D scenario={scenario} />
            </div>
            <div className="split-view-pane">
              <span className="split-pane-label">3D DRONE VISUALIZATION</span>
              <MissionSimulation3D scenario={scenario} />
            </div>
          </div>
        )}
      </div>
      {railOpen && <aside className="right-rail" aria-label="Mission details">
        <PanelDisclosure id="fleet" title="Fleet summary" open={panels.fleet !== false} onToggle={setPanel}><FleetPanel /></PanelDisclosure>
        <PanelDisclosure id="vehicles" title="Vehicles" badge={`${Object.keys(vehicles).length}`} open={panels.vehicles !== false} onToggle={setPanel}><VehiclePanel /></PanelDisclosure>
        <PanelDisclosure id="targets" title="Survivors (autonomous)" badge={`${confirmedTargets}`} open={panels.targets !== false} onToggle={setPanel}><TargetList /></PanelDisclosure>
        <PanelDisclosure id="flight_params" title="Mission flight parameters" open={panels.flight_params !== false} onToggle={setPanel}><FlightParametersPanel /></PanelDisclosure>
        <PanelDisclosure id="health" title="System health" open={panels.health === true} onToggle={setPanel}><HealthPanel /></PanelDisclosure>
        <PanelDisclosure id="video" title="Camera" open={panels.video === true} onToggle={setPanel}>
          <CameraFeedPanel />
        </PanelDisclosure>
      </aside>}
      <div className="bottom-region"><MissionSummary />{panels.timeline !== false ? <EventFeed /> : <button className="timeline-restore" onClick={() => setPanel("timeline", true)}>SHOW MISSION TIMELINE</button>}</div>
      {panels.timeline !== false && <button className="timeline-collapse" onClick={() => setPanel("timeline", false)}>HIDE TIMELINE</button>}
    </main>
  );
}
