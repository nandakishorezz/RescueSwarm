import { lazy, Suspense, useEffect, useState } from "react";

import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
import type { CoverageRequest } from "../types/domain";
import { CopyButton } from "../components/CopyButton";

const SpatialMissionView = lazy(() => import("../components/SpatialMissionView").then((module) => ({ default: module.SpatialMissionView })));

export function SetupPage() {
  const mission = useGcsStore((state) => state.mission);
  const setMission = useGcsStore((state) => state.setMission);
  const offline = useGcsStore((state) => state.offlineStatus);
  const setError = useGcsStore((state) => state.setError);
  const maxAreaHectares = useGcsStore((state) => state.maxAreaHectares);
  const defaults = useGcsStore((state) => state.coverageDefaults);
  const plan = useGcsStore((state) => state.coveragePlan);
  const fleet = useGcsStore((state) => state.fleetStatus);
  const hardware = useGcsStore((state) => state.hardwareGateH1);
  const competition = useGcsStore((state) => state.competitionMode);
  const setMode = useGcsStore((state) => state.setMode);
  const setPlan = useGcsStore((state) => state.setCoveragePlan);
  const [name, setName] = useState("Rescue Search 01");
  const [file, setFile] = useState<File | null>(null);
  const [configuration, setConfiguration] = useState<CoverageRequest | null>(defaults);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { if (defaults && !configuration) setConfiguration(defaults); }, [defaults, configuration]);

  const create = async () => {
    setBusy("Creating mission…");
    try { setMission(await api.createMission(name)); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : "MISSION CREATE FAILED"); }
    finally { setBusy(null); }
  };
  const upload = async () => {
    if (!mission || !file) return;
    setBusy("Validating mission boundary…");
    try { setMission(await api.uploadBoundary(mission.id, file)); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : "BOUNDARY INVALID"); }
    finally { setBusy(null); }
  };
  const validate = async () => {
    if (!mission) return;
    setBusy("Checking mission readiness…");
    try { setMission(await api.validateMission(mission.id)); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : "BOUNDARY INVALID"); }
    finally { setBusy(null); }
  };
  const generatePlan = async () => {
    if (!mission || !configuration) return;
    setBusy("Building coverage and fleet mission definitions…");
    try {
      const generated = await api.generatePlan(mission.id, configuration);
      setPlan(generated);
      setMission(await api.mission(mission.id));
      setError(null);
    } catch (error) { setError(error instanceof Error ? error.message : "COVERAGE PLANNING FAILED"); }
    finally { setBusy(null); }
  };
  const startMission = async () => {
    if (!mission) return;
    if (!window.confirm("Start the coordinated autonomous SITL mission? After START, Competition Mode permits only Safety Abort and Emergency Recall.")) return;
    setBusy("Starting coordinated mission…");
    try { await api.operatorAction(mission.id, "MISSION_START", mission.state); setMode("MISSION"); setError(null); }
    catch (error) { setError(error instanceof Error ? error.message : "MISSION START FAILED"); }
    finally { setBusy(null); }
  };
  const numberField = (key: keyof CoverageRequest, label: string, step = "0.1") => configuration && (
    <label>{label}<input type="number" step={step} value={configuration[key] as number}
      onChange={(event) => setConfiguration({ ...configuration, [key]: Number(event.target.value) })} /></label>
  );
  const startBlockers = [
    !mission && "Create a mission record",
    !mission?.boundary && "Load and validate a GeoJSON or KML boundary",
    !plan?.validation.valid && "Generate a valid search plan",
    mission != null && mission.state !== "READY" && "Mission state must be READY",
    offline?.status !== "READY" && "Offline guard must be READY",
  ].filter(Boolean) as string[];
  return (
    <main className="setup-page">
      <section className="setup-intro"><p>MISSION PREPARATION</p><h1>Prepare the rescue mission.</h1><span>One local workflow from official boundary to autonomous fleet readiness.</span></section>
      {!mission && <section className="first-run" aria-label="First run steps"><b>FIRST RUN</b><span>1 Load boundary</span><span>2 Review fleet</span><span>3 Generate plan</span><span>4 Confirm readiness</span><span>5 Start</span></section>}
      {busy && <div className="loading-state" role="status"><i aria-hidden="true" />{busy}</div>}
      <div className="setup-steps">
        <section className="setup-step">
          <span className="step-number">01</span><div><h2>Mission</h2><p>Create the local evidence record, then load the official GeoJSON or KML boundary.</p>
          <div className="form-row"><input aria-label="Mission name" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /><button disabled={Boolean(busy)} onClick={create}>CREATE MISSION</button></div>
          {mission && <div className="result ok"><span>MISSION CREATED · {mission.name}</span><code title={mission.id}>{mission.id.slice(0, 12)}…</code><CopyButton value={mission.id} label="Copy mission ID" /></div>}
          <div className="form-row boundary-row"><input aria-label="Boundary file" type="file" accept=".geojson,.json,.kml" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button disabled={!mission || !file || Boolean(busy)} onClick={upload}>IMPORT & VALIDATE</button></div>
          {mission?.boundary && <div className={mission.boundary.warnings.length ? "result warning" : "result ok"}>
            <b>{file?.name ?? "IMPORTED MISSION FILE"}</b><span>VALID GEOMETRY · {mission.boundary.area_hectares.toFixed(2)} ha / {maxAreaHectares?.toFixed(2) ?? "—"} ha · {mission.boundary.vertices.length} vertices</span><small>Origin {mission.boundary.origin.latitude.toFixed(6)}, {mission.boundary.origin.longitude.toFixed(6)} · {mission.boundary.source_format.toUpperCase()}</small>
          </div>}</div>
        </section>
        <section className="setup-step">
          <span className="step-number">02</span><div><h2>Fleet</h2><p>Verify simulated vehicle identities before planning. Physical readiness is tracked separately.</p>
          <div className="readiness-grid"><span>Fleet source <b>{fleet?.execution_source ?? "SITL"}</b></span><span>Configured vehicles <b>{fleet?.fleet_size ?? "PLAN PENDING"}</b></span><span>Unique vehicle IDs <b>{fleet ? "PASS" : "PENDING"}</b></span><span>Physical fleet <b>PENDING H1</b></span></div>
          </div>
        </section>
        <section className="setup-step"><span className="step-number">03</span><div><h2>Plan</h2>
          <p>Generate the existing validated coverage plan. Values remain SITL engineering defaults.</p>
          <button disabled={!mission?.boundary || Boolean(busy)} onClick={validate}>VALIDATE MISSION</button>
          {configuration && <div className="planner-fields">
            {numberField("search_altitude_m", "Search altitude (m)")}
            {numberField("lane_spacing_m", "Lane spacing (m)")}
            {numberField("effective_swath_m", "Effective swath (m)")}
            {numberField("edge_margin_m", "Edge margin (m)")}
            {numberField("search_speed_m_s", "Search speed (m/s)")}
            <label>Sweep strategy<select value={configuration.sweep_strategy} onChange={(event) => setConfiguration({ ...configuration, sweep_strategy: event.target.value as CoverageRequest["sweep_strategy"] })}>
              <option value="AUTO_OPTIMIZED">Auto optimized</option><option value="FIXED_ANGLE">Fixed angle</option>
            </select></label>
            {configuration.sweep_strategy === "FIXED_ANGLE" && numberField("fixed_sweep_angle_deg", "Sweep angle (deg)", "1")}
          </div>}
          <button disabled={Boolean(busy) || (mission?.state !== "VALIDATED" && mission?.state !== "READY")} onClick={generatePlan}>GENERATE PLAN</button>
          {plan && <div className="plan-result">
            <span>SAFE AREA <b>{Number(plan.metadata.safe_area_m2 ?? 0).toFixed(0)} m²</b></span>
            <span>SWEEP <b>{plan.sweep_angle_deg.toFixed(1)}°</b></span>
            <span>SEARCH LINES <b>{plan.number_of_search_lines}</b></span>
            <span>WAYPOINTS <b>{plan.path_points_wgs84.length}</b></span>
            <span>DISTANCE <b>{plan.estimated_path_length_m.toFixed(0)} m</b></span>
            <span>EST. TIME <b>{(plan.estimated_search_time_s / 60).toFixed(1)} min</b></span>
            <span>COVERAGE <b>{plan.validation.planned_coverage_percent.toFixed(1)}%</b></span>
            <span>VALIDATION <b className={plan.validation.valid ? "ok" : "danger"}>{plan.validation.valid ? "PASS" : "FAIL"}</b></span>
          </div>}
          {plan && <div className="setup-preview"><div className="panel-title"><h2>PLAN PREVIEW</h2><span className="sim-tag">2D / 3D · VIEW ONLY</span></div><Suspense fallback={<div className="loading-state">LOADING LOCAL MAP…</div>}><SpatialMissionView /></Suspense></div>}
        </div></section>
        <section className="setup-step"><span className="step-number">04</span><div><h2>Payload</h2><p>Inventory is simulated. No servo, GPIO, or physical release path is present.</p><div className="readiness-grid"><span>Fleet payload slots <b>{fleet ? Object.values(fleet.payload_inventory ?? {}).reduce((sum, value) => sum + value, 0) : "PENDING FLEET PLAN"}</b></span><span>Execution <b>SIMULATED ONLY</b></span><span>Physical mechanism <b>PENDING H1</b></span><span>Verification <b>SYNTHETIC</b></span></div></div></section>
        <section className="setup-step"><span className="step-number">05</span><div><h2>Readiness</h2><p>SITL readiness and physical evidence are intentionally separate.</p>
          <div className="readiness-grid"><span>Mission file <b>{mission?.boundary ? "PASS" : "NOT READY"}</b></span><span>Coverage plan <b>{plan?.validation.valid ? "PASS" : "NOT READY"}</b></span><span>Offline local network <b>{offline?.status ?? "CHECKING"}</b></span><span>Competition lock <b>{competition ? "ON" : "OFF"}</b></span><span>SITL software <b>{startBlockers.length ? "NOT READY" : "READY"}</b></span><span>Physical validation <b>{hardware?.state ?? "PENDING"}</b></span></div>
          {startBlockers.length > 0 && <div className="readiness-blockers"><b>START BLOCKED</b><ul>{startBlockers.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>}
          {!startBlockers.length && <div className="result ok">SITL READY · PHYSICAL VALIDATION REMAINS PENDING</div>}
        </div></section>
        <section className="setup-step"><span className="step-number">06</span><div><h2>Start</h2><p>One operator action begins the coordinated mission. No per-drone controls are exposed.</p>
          <button className="mission-start-button" disabled={Boolean(busy) || startBlockers.length > 0} onClick={startMission}>START MISSION</button>
          <small className="button-help">Consequential action · confirmation required</small>
        </div></section>
      </div>
      <aside className="setup-start-dock" aria-label="Mission start readiness"><div><span>SITL READINESS</span><b>{startBlockers.length ? `${startBlockers.length} BLOCKER${startBlockers.length === 1 ? "" : "S"}` : "READY TO START"}</b></div><button className="mission-start-button" disabled={Boolean(busy) || startBlockers.length > 0} onClick={startMission}>START MISSION</button></aside>
    </main>
  );
}
