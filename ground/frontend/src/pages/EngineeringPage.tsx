import { useEffect, useState } from "react";

import { SpatialMissionView } from "../components/SpatialMissionView";
import { SpatialInspector } from "../components/SpatialInspector";
import { ResiliencePanel } from "../components/ResiliencePanel";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

const actions = [
  ["Start simulation", "start"], ["Pause", "pause"], ["Resume", "resume"], ["Reset", "reset"],
  ["Inject candidate", "inject-target"], ["Confirm target", "confirm-target"],
  ["Low battery", "low-battery"], ["GPS degradation", "gps-degradation"],
  ["Link loss", "link-loss"], ["Simulate delivery", "simulate-delivery"], ["Complete mission", "complete"],
] as const;

export function EngineeringPage() {
  const mission = useGcsStore((state) => state.mission);
  const envelope = useGcsStore((state) => state.lastEnvelope);
  const offline = useGcsStore((state) => state.offlineStatus);
  const setError = useGcsStore((state) => state.setError);
  const plan = useGcsStore((state) => state.coveragePlan);
  const [sitlStatus, setSitlStatus] = useState<Record<string, unknown> | null>(null);
  const [visionStatus, setVisionStatus] = useState<Record<string, unknown> | null>(null);
  const [geolocationStatus, setGeolocationStatus] = useState<Record<string, unknown> | null>(null);
  const [hardwareStatus, setHardwareStatus] = useState<Record<string, unknown> | null>(null);
  const [fleetStatus, setFleetStatus] = useState<Record<string, unknown> | null>(null);
  const [launchAltitude, setLaunchAltitude] = useState("");
  const [targetEast, setTargetEast] = useState("20");
  const [targetNorth, setTargetNorth] = useState("14");
  const [section, setSection] = useState("SYSTEM");
  const sections = ["SYSTEM", "VEHICLES", "VISION", "SPATIAL", "CAMERA", "TARGETS", "FAILURES", "NETWORK", "STORAGE", "SITL"];
  const refreshSitl = () => api.sitlStatus(mission?.id).then(setSitlStatus).catch(() => setSitlStatus(null));
  const refreshVision = () => api.visionStatus().then(setVisionStatus).catch(() => setVisionStatus(null));
  const refreshGeolocation = () => api.geolocationStatus().then(setGeolocationStatus).catch(() => setGeolocationStatus(null));
  const refreshHardware = () => api.hardwareValidation().then(setHardwareStatus).catch(() => setHardwareStatus(null));
  const refreshFleet = () => mission && api.fleet(mission.id).then((value) => setFleetStatus(value as unknown as Record<string, unknown>)).catch(() => setFleetStatus(null));
  useEffect(() => {
    refreshSitl(); refreshVision(); refreshGeolocation(); refreshHardware(); refreshFleet();
    const timer = window.setInterval(() => { refreshSitl(); refreshVision(); refreshGeolocation(); refreshHardware(); refreshFleet(); }, 3000);
    return () => window.clearInterval(timer);
  }, [mission?.id]);
  const invoke = async (action: string) => {
    if (!mission) return;
    try { await api.simulator(mission.id, action); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : "SIMULATOR ACTION FAILED"); }
  };
  const runRescue = async () => {
    if (!mission) return;
    try {
      const east = Number(targetEast); const north = Number(targetNorth);
      if (!Number.isFinite(east) || !Number.isFinite(north)) throw new Error("Enter valid synthetic target ENU coordinates");
      await api.executeSitlRescue(mission.id, east, north);
      setError(null); refreshSitl();
    } catch (error) { setError(error instanceof Error ? error.message : "SITL RESCUE FAILED"); }
  };
  const invokeSitl = async (action: "start" | "run" | "abort" | "stop") => {
    if (!mission) return;
    try {
      if (action === "start") {
        const altitude = Number(launchAltitude);
        if (!Number.isFinite(altitude)) throw new Error("Enter the mission site's mean-sea-level launch altitude");
        await api.startSitl(mission.id, altitude);
      } else if (action === "run") await api.executeSitl(mission.id);
      else if (action === "abort") await api.abortSitl(mission.id);
      else await api.stopSitl();
      setError(null); refreshSitl();
    } catch (error) { setError(error instanceof Error ? error.message : "SITL ACTION FAILED"); }
  };
  const createFleetPlan = async () => {
    if (!mission) return;
    try { await api.fleetPlan(mission.id, 2); setError(null); refreshFleet(); }
    catch (error) { setError(error instanceof Error ? error.message : "FLEET PLAN FAILED"); }
  };
  return (
    <main className="engineering-page">
      <section className="engineering-map"><SpatialMissionView /></section>
      <aside className="engineering-tools">
        <div className="engineering-heading"><div><span>ENGINEERING WORKSPACE</span><h1>{section}</h1></div><div className="result warning">PHYSICAL VALIDATION H1 · PENDING</div></div>
        <nav className="engineering-tabs" aria-label="Engineering sections">{sections.map((item) => <button className={section === item ? "active" : ""} onClick={() => setSection(item)} key={item}>{item}</button>)}</nav>
        {(section === "SPATIAL" || section === "CAMERA") && <SpatialInspector />}
        {(section === "SYSTEM" || section === "FAILURES") && <ResiliencePanel />}
        {section === "VEHICLES" && <section className="sitl-block"><div className="panel-title"><h2>MULTI-ARDUCOPTER FLEET</h2><span className="sim-tag">STAGE 9S · SITL ONLY</span></div>
          <p>One global plan becomes deterministic per-vehicle sectors. Commands remain isolated to loopback SITL endpoints.</p>
          <button disabled={!mission || !plan?.validation.valid} onClick={createFleetPlan}>CREATE 2-DRONE FLEET PLAN</button>
          <pre>{JSON.stringify(fleetStatus, null, 2)}</pre>
        </section>}
        {section === "FAILURES" && <><div className="panel-title"><h1>GCS EVENT SIMULATOR</h1><span className="sim-tag">DEVELOPMENT ONLY</span></div>
        <p>No command in this panel is connected to a real aircraft.</p>
        <div className="control-grid">{actions.map(([label, action]) => <button disabled={!mission} onClick={() => invoke(action)} key={action}>{label}</button>)}</div></>}
        {section === "SITL" && <><section className="sitl-block"><div className="panel-title"><h2>ARDUCOPTER SITL</h2><span className="sim-tag">SIMULATION COMMANDS ONLY</span></div>
          <p>This control path rejects serial, COM, LAN, and public endpoints.</p>
          <label>Launch altitude MSL (m)<input type="number" value={launchAltitude} onChange={(event) => setLaunchAltitude(event.target.value)} /></label>
          <div className="control-grid">
            <button disabled={!mission || !launchAltitude} onClick={() => invokeSitl("start")}>START OFFICIAL SITL</button>
            <button disabled={!mission || !plan?.validation.valid} onClick={() => invokeSitl("run")}>RUN SITL SEARCH MISSION</button>
            <button className="abort-button" disabled={!mission} onClick={() => invokeSitl("abort")}>ABORT SITL → RTL</button>
            <button onClick={() => invokeSitl("stop")}>STOP SITL PROCESS</button>
          </div>
          <pre>{JSON.stringify(sitlStatus, null, 2)}</pre>
        </section>
        <section className="sitl-block"><div className="panel-title"><h2>AUTONOMOUS RESCUE</h2><span className="sim-tag">SITL + SYNTHETIC TARGET/PAYLOAD</span></div>
          <p>One autonomous AUTO → GUIDED → AUTO → RTL flow. It cannot connect to physical serial hardware.</p>
          <label>Target East (m)<input type="number" value={targetEast} onChange={(event) => setTargetEast(event.target.value)} /></label>
          <label>Target North (m)<input type="number" value={targetNorth} onChange={(event) => setTargetNorth(event.target.value)} /></label>
          <button disabled={!mission || !plan?.validation.valid} onClick={runRescue}>RUN AUTONOMOUS SITL RESCUE</button>
        </section></>}
        {section === "VISION" && <><section className="vision-block"><div className="panel-title"><h2>GEOLOCATION</h2><span className="sim-tag">READ ONLY</span></div>
          <p>Calibration, clock, pose and target-coordinate health. Stage 5 has no flight-control commands.</p>
          {!geolocationStatus?.connected && <div className="result warning">NOT CONNECTED · PHYSICAL ACCURACY NOT MEASURED</div>}
          <div className="vision-metrics">
            <span>Calibration <b>{String(geolocationStatus?.calibration_health ?? "MISSING")}</b></span>
            <span>Clock sync <b>{String(geolocationStatus?.clock_sync_health ?? "UNSYNCED")}</b></span>
            <span>Pose sync <b>{String(geolocationStatus?.pose_sync_health ?? "POSE STALE")}</b></span>
            <span>Geolocation <b>{String(geolocationStatus?.geolocation_health ?? "NOT READY")}</b></span>
            <span>Physical accuracy <b>{String(geolocationStatus?.physical_accuracy ?? "NOT MEASURED")}</b></span>
          </div>
        </section>
        <section className="vision-block"><div className="panel-title"><h2>VISION AGENT</h2><span className="sim-tag">READ ONLY</span></div>
          <p>Pi 5 camera and detector health. The vision process has no flight-control interface.</p>
          {!visionStatus?.connected && <div className="result warning">NOT CONNECTED · PHYSICAL CAMERA NOT VERIFIED</div>}
          <div className="vision-metrics">
            <span>Source <b>{String(visionStatus?.source ?? "NOT CONNECTED")}</b></span>
            <span>Camera <b>{String((visionStatus?.camera_health as { state?: string } | undefined)?.state ?? "UNKNOWN")}</b></span>
            <span>Detector <b>{String((visionStatus?.detector_health as { state?: string } | undefined)?.state ?? "UNKNOWN")}</b></span>
            <span>Capture FPS <b>{String(visionStatus?.capture_fps ?? "—")}</b></span>
            <span>P95 latency <b>{String(visionStatus?.p95_latency_ms ?? "—")}</b></span>
            <span>Dropped <b>{String(visionStatus?.dropped_frames ?? "—")}</b></span>
            <span>Temperature <b>{String(visionStatus?.temperature_c ?? "—")}</b></span>
          </div>
        </section></>}
        {section === "SYSTEM" && <section className="vision-block"><div className="panel-title"><h2>HARDWARE VALIDATION</h2><span className="sim-tag">PHYSICAL BENCH · READ ONLY</span></div>
          <div className="result warning">HARDWARE GATE H1 · PENDING — HARDWARE TEMPORARILY UNAVAILABLE</div>
          <p>Newest persisted Stage 6 evidence. This panel contains no aircraft controls.</p>
          {!hardwareStatus?.connected && <div className="result warning">PHYSICAL VALIDATION REQUIRED</div>}
          <div className="vision-metrics">
            <span>Run <b>{String(hardwareStatus?.run_id ?? "NONE")}</b></span>
            <span>Pi 5 <b>{String(hardwareStatus?.pi ?? "NOT_TESTED")}</b></span>
            <span>Pixhawk <b>{String(hardwareStatus?.pixhawk ?? "NOT_TESTED")}</b></span>
            <span>GPS <b>{String(hardwareStatus?.gps ?? "NOT_TESTED")}</b></span>
            <span>Camera <b>{String(hardwareStatus?.camera ?? "NOT_TESTED")}</b></span>
            <span>Power <b>{String(hardwareStatus?.power ?? "UNKNOWN")}</b></span>
            <span>TIMESYNC <b>{String(hardwareStatus?.timesync ?? "NOT_TESTED")}</b></span>
            <span>Frame/Pose <b>{String(hardwareStatus?.frame_pose_sync ?? "NOT_TESTED")}</b></span>
            <span>Calibration <b>{String(hardwareStatus?.calibration_id ?? "NOT_TESTED")}</b></span>
            <span>Physical flight <b>NO</b></span>
          </div>
        </section>}
        {section === "NETWORK" && <section className="diagnostic-block"><h2>OFFLINE LOCAL NETWORK</h2><p>Public internet, cloud APIs, remote maps, and external telemetry are prohibited.</p><pre>{JSON.stringify(offline, null, 2)}</pre></section>}
        {section === "STORAGE" && <section className="diagnostic-block"><h2>LATEST NORMALIZED ENVELOPE</h2><p>Bounded diagnostic view; raw MAVLink is not sent to React.</p><pre>{JSON.stringify(envelope, null, 2)}</pre></section>}
        {section === "TARGETS" && <section className="diagnostic-block"><h2>TARGET DIAGNOSTICS</h2><p>Use Mission or Review for the canonical target list. Engineering target injection remains in FAILURES and is unavailable in Competition Mode.</p></section>}
      </aside>
    </main>
  );
}
