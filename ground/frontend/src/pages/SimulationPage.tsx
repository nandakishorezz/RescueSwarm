import { useEffect, useMemo, useState } from "react";

import { SimulationWorldPreview, type PreviewSurvivor } from "../components/SimulationWorldPreview";
import { SimulationWorkspaceMap } from "../components/SimulationWorkspaceMap";
import { api, simulationCameraFrameUrl } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

type JsonMap = Record<string, any>;
type Section = "DRONE" | "CAMERA" | "SENSORS" | "ENVIRONMENT" | "MISSION" | "SURVIVORS";
type LabPanel = "CONFIGURATION" | "CAMERA" | "PERCEPTION" | "OBSERVER" | "METRICS";
interface PlacedSurvivor extends PreviewSurvivor { latitude: number; longitude: number; }
interface EstimatedPose { position_enu_m: [number, number, number]; quaternion_body_to_ned: [number, number, number, number]; }

// V3's accepted live-search evidence was captured at 7.78 m and evaluated at
// 640 px. Keep the default search inside that proven operating envelope.
const V3_SEARCH_ALTITUDE_M = 8.0;
// The fixed body camera moves several metres off nadir while the vehicle
// accelerates and turns.  A live two-person run showed that 20% geometric
// overlap could leave a person with only one edge/turn view, while repeated
// neighbouring passes produced stable V3 detections.  Fifty percent keeps
// every searchable point within two adjacent 8 m swaths and supplies the
// independent frames required by temporal confirmation.
const SEARCH_SWATH_OVERLAP = 0.50;

// Phase-2/V3 captured upright Gazebo actors side-on at -90 degrees.  A
// top-down 0-degree actor is strongly foreshortened during a direct overflight
// and produced only one usable detection in the live acceptance run.  The
// rotated lying pose is already fully visible from above and is kept at the
// proven 0-degree orientation.
export const v3ActorYawDeg = (bodyState: string): number =>
  bodyState.toLowerCase() === "lying" ? 0 : -90;

const scalar = (profile: JsonMap | null, key: string, fallback: number) => Number(profile?.[key]?.value ?? fallback);
const eulerQuaternion = (rollDeg: number, pitchDeg: number, yawDeg: number): [number, number, number, number] => {
  const [roll, pitch, yaw] = [rollDeg, pitchDeg, yawDeg].map((value) => value * Math.PI / 180 / 2);
  const [cr, sr, cp, sp, cy, sy] = [Math.cos(roll), Math.sin(roll), Math.cos(pitch), Math.sin(pitch), Math.cos(yaw), Math.sin(yaw)];
  return [cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy];
};

export function SimulationPage() {
  const mission = useGcsStore((state) => state.mission);
  const coverage = useGcsStore((state) => state.coveragePlan);
  const competition = useGcsStore((state) => state.competitionMode);
  const [section, setSection] = useState<Section>("DRONE");
  const [labPanel, setLabPanel] = useState<LabPanel>("CONFIGURATION");
  const [drone, setDrone] = useState<JsonMap | null>(null);
  const [camera, setCamera] = useState<JsonMap | null>(null);
  const [environment, setEnvironment] = useState<JsonMap | null>(null);
  const [sensorProfiles, setSensorProfiles] = useState<JsonMap>({});
  const [sensorProfileId, setSensorProfileId] = useState("REFERENCE_NOMINAL");
  const [sensorSeed, setSensorSeed] = useState(31003);
  const [survivors, setSurvivors] = useState<PlacedSurvivor[]>([]);
  const [simStatus, setSimStatus] = useState<JsonMap>({ state: "IDLE" });
  const [observerTruth, setObserverTruth] = useState(false);
  const [observerData, setObserverData] = useState<JsonMap | null>(null);
  const [perception, setPerception] = useState<JsonMap | null>(null);
  const [frameNonce, setFrameNonce] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [workspaceStep, setWorkspaceStep] = useState(1);
  const [polygon, setPolygon] = useState<[number, number][]>([]);
  const [mapCenter, setMapCenter] = useState<[number, number]>([77.5946, 12.9716]);
  const [startPosition, setStartPosition] = useState<{ lng: number; lat: number } | null>(null);
  const setMode = useGcsStore((state) => state.setMode);
  const setViewMode = useGcsStore((state) => state.setViewMode);
  const setMissionStore = useGcsStore((state) => state.setMission);
  const setCoveragePlan = useGcsStore((state) => state.setCoveragePlan);
  const clearSimulationState = useGcsStore((state) => state.clearSimulationState);

  const [message, setMessage] = useState("Loading locally bundled reference profiles…");
  const [selectedSurvivor, setSelectedSurvivor] = useState<string | null>(null);

  useEffect(() => {
    clearSimulationState();
  }, [clearSimulationState]);

  useEffect(() => {
    Promise.all([api.simulationReferenceProfiles(), api.simulationStatus()]).then(([profiles, status]) => {
      setDrone(profiles.drone as JsonMap); setCamera(profiles.camera as JsonMap); setEnvironment(profiles.environment as JsonMap);
      setSensorProfiles((profiles.sensor_profiles ?? {}) as JsonMap);
      setSimStatus(status as JsonMap); setMessage("Reference profiles loaded · every unmeasured value retains provenance");
    }).catch((reason) => setMessage(reason instanceof Error ? reason.message : "Simulation service unavailable"));
  }, []);

  useEffect(() => {
    if (!observerTruth || simStatus.state !== "RUNNING") return;
    Promise.all([api.simulationObserverTruth(), api.simulationPerceptionObserver()])
      .then(([truth, metrics]) => setObserverData({ truth, metrics }))
      .catch((reason) => setMessage(String(reason)));
  }, [observerTruth, simStatus.state]);

  useEffect(() => {
    const refresh = () => api.simulationPerceptionLatest().then((value) => {
      setPerception(value as JsonMap); setFrameNonce(Date.now());
    }).catch(() => undefined);
    refresh();
    const timer = window.setInterval(refresh, simStatus.state === "RUNNING" ? 1500 : 5000);
    return () => window.clearInterval(timer);
  }, [simStatus.state]);

  useEffect(() => {
    if (simStatus.state !== "RUNNING") return;
    const timer = window.setInterval(() => api.simulationStatus().then((value) => setSimStatus(value as JsonMap)).catch(() => undefined), 500);
    return () => window.clearInterval(timer);
  }, [simStatus.state]);

  const locked = Boolean(simStatus.config_locked) || (simStatus.state !== "IDLE" && simStatus.state !== "FAILED" && simStatus.state !== "OFF" && simStatus.state !== "READY");
  const previewBoundary = useMemo<[number, number][]>(() => {
    const points = coverage?.safe_polygon_enu ?? [];
    return points.map((point) => [point.east_m, point.north_m]);
  }, [coverage]);
  const visibleSurvivors = locked && !observerTruth ? [] : survivors;
  const cameraIntrinsics = useMemo(() => ({
    fx: scalar(camera?.intrinsics ?? null, "fx", 800), fy: scalar(camera?.intrinsics ?? null, "fy", 800),
    cx: scalar(camera?.intrinsics ?? null, "cx", 640), cy: scalar(camera?.intrinsics ?? null, "cy", 400),
    width: Number(camera?.resolution_width ?? 1280), height: Number(camera?.resolution_height ?? 800),
  }), [camera]);
  const cameraQuaternion = useMemo(() => (camera?.mount_rotation_camera_to_body?.value ?? [Math.SQRT1_2, 0, 0, Math.SQRT1_2]) as [number, number, number, number], [camera]);
  const liveEstimate = simStatus.estimated_vehicle as EstimatedPose | undefined;
  const sensorProfile = sensorProfiles[sensorProfileId] as JsonMap | undefined;

  if (competition) return <main className="simulation-disabled"><strong>SIMULATION LAB DISABLED</strong><p>Competition Mode exposes no simulation or engineering controls.</p></main>;

  const updateScalar = (target: JsonMap | null, setter: (value: JsonMap) => void, key: string, value: number) => {
    if (!target || locked) return;
    setter({ ...target, [key]: { ...target[key], value } });
  };
  const updateCameraEuler = (key: "mount_roll_deg" | "mount_pitch_deg" | "mount_yaw_deg", value: number) => {
    if (!camera || locked) return;
    const next = { ...camera, [key]: { ...camera[key], value } };
    next.mount_rotation_camera_to_body = {
      ...camera.mount_rotation_camera_to_body,
      value: eulerQuaternion(scalar(next, "mount_roll_deg", 0), scalar(next, "mount_pitch_deg", 0), scalar(next, "mount_yaw_deg", 0)),
      provenance: "ENGINEERING_ESTIMATE",
    };
    setCamera(next);
  };
  const updateRotorLayout = (layout: string) => {
    if (!drone || locked) return;
    const counts: Record<string, number> = { QUAD_X: 4, QUAD_PLUS: 4, HEXA_X: 6, OCTA_X: 8 };
    const count = counts[layout] ?? Number(drone.rotor_count);
    const template = drone.rotors?.[0];
    const arm = scalar(drone, "arm_length_m", .255);
    const offset = layout.endsWith("_X") ? Math.PI / count : 0;
    const rotors = template ? Array.from({ length: count }, (_, index) => {
      const angle = offset + 2 * Math.PI * index / count;
      return {
        ...template, rotor_id: `rotor_${index}`,
        position_body_frd: { ...template.position_body_frd, value: [Math.cos(angle) * arm, Math.sin(angle) * arm, template.position_body_frd.value[2]], provenance: "ENGINEERING_ESTIMATE" },
        rotation_direction: index % 2 ? "CW" : "CCW",
      };
    }) : drone.rotors;
    setDrone({ ...drone, rotor_layout: layout, rotor_count: count, rotors });
  };

  const placeSurvivor = async (eastM: number, northM: number) => {
    if (locked || !mission?.boundary?.origin) return;
    try {
      const world = await api.simulationEnuToWorld({
        origin_latitude: mission.boundary.origin.latitude,
        origin_longitude: mission.boundary.origin.longitude,
        origin_altitude_m: mission.boundary.origin.altitude_m ?? 0,
        east_m: eastM, north_m: northM, up_m: 0,
      }) as JsonMap;
      const wgs = world.wgs84 as JsonMap;
      setSurvivors((current) => selectedSurvivor ? current.map((item) => item.id === selectedSurvivor ? {
        ...item, eastM, northM, latitude: Number(wgs.latitude), longitude: Number(wgs.longitude),
      } : item) : [...current, {
        id: `survivor_${String(current.length + 1).padStart(2, "0")}`,
        eastM, northM, latitude: Number(wgs.latitude), longitude: Number(wgs.longitude), bodyState: current.length % 2 ? "lying" : "standing",
      }]);
      setMessage(`${selectedSurvivor ? "Moved" : "Placed"} hidden survivor at E ${eastM.toFixed(1)} m · N ${northM.toFixed(1)} m (observer setup only)`);
      setSelectedSurvivor(null);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Coordinate conversion failed"); }
  };

  const prepare = async () => {
    if (!drone || !camera || !environment || polygon.length < 3) { setMessage("Ensure drone, camera, environment are loaded and a valid polygon is drawn"); return; }
    const runId = `stage13sim_${Date.now()}`;
    // Compute approximate area in m2 for dimension scaling
    const p1 = polygon[0];
    const p2 = polygon[1];
    const dist = Math.sqrt(Math.pow(p1[0]-p2[0], 2) + Math.pow(p1[1]-p2[1], 2)) * 111320;
    const approxArea = dist * dist * 4;

    // Stop any existing simulation first so prepare() is not blocked
    const existingState = simStatus.state;
    if (existingState === "RUNNING" || existingState === "READY" || existingState === "STARTING_GAZEBO" || existingState === "WAITING_FOR_READY") {
      setMessage("Stopping previous simulation…");
      try { const stopped = await api.stopSimulation(); setSimStatus(stopped as JsonMap); } catch { /* ignore */ }
      // Brief wait for cleanup
      await new Promise(resolve => setTimeout(resolve, 1000));
    }

    // Ensure a real mission exists before preparing
    // ALWAYS force a new mission record for a new simulation run so historical events are cleared
    let activeMission = null;
    if (!activeMission) {
      try {
        setMessage("Creating mission record…");
        const resp = await fetch("http://127.0.0.1:8000/api/missions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "Simulated Mission" }),
        });
        if (resp.ok) {
          activeMission = await resp.json();
          if (activeMission) setMissionStore(activeMission);
        }
      } catch {
        // Non-fatal: proceed with fallback ID
      }
    }

    const missionId = activeMission?.id ?? "simulated_mission";

    // Attach the drawn polygon to the mission so fleetPlan generates waypoints here!
    if (activeMission && polygon.length >= 3) {
      try {
        setMessage("Attaching boundary to mission…");
        const boundaryGeoJson = {
          type: "FeatureCollection",
          features: [{
            type: "Feature",
            properties: {},
            geometry: {
              type: "Polygon",
              coordinates: [[...polygon.map(p => [p[0], p[1]]), [polygon[0][0], polygon[0][1]]]]
            }
          }]
        };
        const blob = new Blob([JSON.stringify(boundaryGeoJson)], { type: "application/json" });
        const file = new File([blob], "boundary.geojson", { type: "application/json" });
        const updatedMission = await api.uploadBoundary(missionId, file);
        if (updatedMission) {
          activeMission = updatedMission;
          setMissionStore(activeMission);
        }
      } catch (err) {
        console.error("Failed to attach boundary", err);
      }
    }

    // Validate mission so generatePlan can run (requires VALIDATED state)
    try {
      setMessage("Validating mission boundary…");
      const validated = await fetch(`http://127.0.0.1:8000/api/missions/${missionId}/validate`, { method: "POST" });
      if (validated.ok) {
        const vm = await validated.json();
        if (vm) { activeMission = vm; setMissionStore(vm); }
      }
    } catch (err) {
      console.error("Mission validation failed", err);
    }

    // Auto-save scenario so MapView or reloads can use the placed survivors
    localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({ drone, camera, environment, survivors, sensorProfileId, sensorSeed, startPosition, polygon }));

    const missionWorld = {
      schema_version: "1.0", mission_id: missionId,
      boundary_wgs84: polygon.map((point) => [point[1], point[0]]),
      origin_wgs84: startPosition ? [startPosition.lat, startPosition.lng, 0] : [polygon[0][1], polygon[0][0], 0],
      ground_elevation_m: { value: 0, unit: "m", provenance: "SIMULATION_ONLY" },
      world_dimensions_m: { value: [4000, 4000, 80], unit: "m", provenance: "ENGINEERING_ESTIMATE" },
      launch_zone_width_m: { value: 3.6576, unit: "m", provenance: "REFERENCE_PRESET" },
      launch_zone_length_m: { value: 3.6576, unit: "m", provenance: "REFERENCE_PRESET" },
      terrain_mode: "FLAT", lighting: "DAY", environment_profile_id: environment.profile_id,
    };
    const survivorScenario = { schema_version: "1.0", scenario_id: `${runId}_hidden`, survivors: survivors.map((item) => ({
      survivor_id: item.id, latitude: item.latitude, longitude: item.longitude, up_m: 0,
      yaw_deg: v3ActorYawDeg(item.bodyState),
      // The accepted V3 pixels were generated from the bundled Gazebo human
      // actor, not the orange primitive.  Preserve body state (including a
      // rotated lying actor) while keeping scenario truth outside autonomy.
      body_state: item.bodyState, visual_variant: "GAZEBO_ACTOR_STANDING_CC_BY_4_0", occlusion_profile: "NONE", enabled: true,
    })) };
    const config = {
      schema_version: "1.0", config_id: `${runId}_config`, drone_profile_id: drone.profile_id,
      camera_profile_id: camera.camera_id, environment_profile_id: environment.profile_id,
      mission_world_profile_id: missionId, survivor_scenario_id: survivorScenario.scenario_id,
      random_seed: Number(environment.random_seed), execution_source: "SITL_GAZEBO",
    };
    try {
      setMessage("Validating profiles and generating local Gazebo world…");
      const result = await api.prepareSimulation({
        run_id: runId, config, drone, camera, environment, mission: missionWorld,
        survivors: survivorScenario, sensor_profile: sensorProfile,
        sensor_seeds: { sensor_seed: sensorSeed, wind_seed: Number(environment.wind?.random_seed), scenario_seed: Number(environment.random_seed) },
      });
      setSimStatus(result as JsonMap); setMessage("READY · generated world, vehicle, camera, wind schedule and isolated truth artifacts");
    } catch (reason) { 
      console.error("prepare() api.prepareSimulation threw", reason);
      setMessage(reason instanceof Error ? reason.message : "Simulation preparation failed"); 
    }
  };

  const bootEnvironment = async () => {
    if (!window.confirm("Start the local Gazebo + ArduPilot SITL simulation? No physical aircraft will be accessed.")) return;
    setObserverTruth(false);
    setObserverData(null);
    try { 
       setMessage("Starting Gazebo and ArduPilot SITL on loopback\u2026"); 
       await api.startSimulation();
       
       setMessage("Waiting for Gazebo and ArduPilot to become ready\u2026 (this takes ~30s)");
       let ready = false;
       for (let i = 0; i < 60; i++) {
         await new Promise(resolve => setTimeout(resolve, 2000));
         const pollStatus = await api.simulationStatus() as JsonMap;
         setSimStatus(pollStatus);
         const st = String(pollStatus.state ?? "");
         if (st === "RUNNING") { ready = true; break; }
         if (st === "FAILED") throw new Error(pollStatus.error ? String(pollStatus.error) : "Simulation startup failed");
         setMessage(`Waiting for Gazebo and ArduPilot\u2026 (${st}) \u2014 ${i * 2}s elapsed`);
       }
       if (!ready) throw new Error("Simulation did not become RUNNING within 120 seconds");

       setMessage("Generating Stage 3 coverage plan...");
       const currentMission = useGcsStore.getState().mission;
       if (!currentMission) throw new Error("No active mission record found");
       const horizontalFov = scalar(camera, "horizontal_fov", 1.3494818844471055);
       const verticalFov = scalar(camera, "vertical_fov", 0.9272952180016122);
       const flightParams = currentMission.flight_parameters;
       const searchAlt = flightParams?.search_altitude_m ?? V3_SEARCH_ALTITUDE_M;
       const searchSpeed = flightParams?.search_speed_m_s ?? 5.0;
       const swathOverlap = (flightParams?.search_swath_overlap_percent ?? (SEARCH_SWATH_OVERLAP * 100)) / 100;
       const alongTrackFootprintM = 2 * searchAlt * Math.tan(horizontalFov / 2);
       const crossTrackFootprintM = 2 * searchAlt * Math.tan(verticalFov / 2);
       
       const newPlan = await api.generatePlan(currentMission.id, {
         search_altitude_m: searchAlt,
         lane_spacing_m: crossTrackFootprintM * (1 - swathOverlap),
         effective_swath_m: crossTrackFootprintM,
         along_track_footprint_m: alongTrackFootprintM,
         edge_margin_m: 0.0,
         search_speed_m_s: searchSpeed,
         sweep_strategy: "AUTO_OPTIMIZED" as any,
         fixed_sweep_angle_deg: 0.0,
         turn_penalty_m: 10.0,
         min_planned_coverage_percent: 99.9,
         max_waypoints: 500,
         flight_parameters: flightParams ?? undefined,
       });
       if (newPlan) setCoveragePlan(newPlan as any);

       setMessage("Generating fleet partition...");
       try { await api.fleetPlan(currentMission.id, 2); } catch { /* non-fatal: existing plan used */ }
       
       localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({ drone, camera, environment, survivors, sensorProfileId, sensorSeed, startPosition, polygon }));
       console.log("[SIMULATION] mission prepared | id:", currentMission.id, "| boundary:", polygon.length, "| survivors:", survivors.length, "| start:", JSON.stringify(startPosition));
       setMessage("ENVIRONMENT READY — Navigating to Mission Tab…");
       // Auto-navigate: operator must NOT need to manually click
       setTimeout(() => {
         localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({ drone, camera, environment, survivors, sensorProfileId, sensorSeed, startPosition, polygon }));
         console.log("[SIMULATION] auto-navigating to MissionPage");
         setMode("MISSION");
         setViewMode("2D");
       }, 800);
    }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "Simulation start failed"); }
  };
  
  const goToMissionTab = () => {
       localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({ drone, camera, environment, survivors, sensorProfileId, sensorSeed, startPosition, polygon }));
       setMode("MISSION"); 
       setViewMode("2D"); 
  };
  const stop = async () => { setSimStatus(await api.stopSimulation() as JsonMap); setObserverTruth(false); setMessage("Simulation stopped; owned processes cleaned up"); };
  const saveScenario = () => {
    localStorage.setItem("rescueswarm.simulationScenario", JSON.stringify({ drone, camera, environment, survivors, sensorProfileId, sensorSeed, startPosition, polygon }));
    setMessage("Scenario saved locally in this browser");
  };
  const loadScenario = () => {
    const raw = localStorage.getItem("rescueswarm.simulationScenario");
    if (!raw || locked) return;
    const value = JSON.parse(raw) as JsonMap; setDrone(value.drone); setCamera(value.camera); setEnvironment(value.environment); setSurvivors(value.survivors ?? []); setSensorProfileId(value.sensorProfileId ?? "REFERENCE_NOMINAL"); setSensorSeed(Number(value.sensorSeed ?? 31003)); setMessage("Local scenario restored");
  };

  return <main className="simulation-page workspace-mode">
    <header className="simulation-hero">
      <div><span>MISSION SIMULATION WORKSPACE</span><h1>PLAN & LAUNCH</h1><p>Step {workspaceStep} of 8</p></div>
      <div className="simulation-safety"><b>NO PHYSICAL AIRCRAFT</b><span>SITL_GAZEBO</span><span>H1 PENDING</span></div>
    </header>

    <div className="simulation-workspace">
      {workspaceStep === 1 && (
        <section className="wizard-step">
          <h2>1. Drone & Sensor Configuration</h2>
          <div className="wizard-content config-grid">
            <div>
              <h3>Drone Configuration</h3>
              <p className="provenance-note">REFERENCE PRESET</p>
              <label>Frame layout<select value={String(drone?.rotor_layout ?? "QUAD_X")} disabled={locked} onChange={(event) => updateRotorLayout(event.target.value)}><option>QUAD_X</option><option>QUAD_PLUS</option><option>HEXA_X</option><option>OCTA_X</option></select></label>
              <label>Rotor count<input type="number" value={Number(drone?.rotor_count ?? 4)} disabled /></label>
              {(["mass_kg", "cruise_speed_mps"] as const).map((key) => <label key={key}>{key.replaceAll("_", " ")}<input type="number" step="0.01" value={scalar(drone, key, 0)} disabled={locked} onChange={(event) => updateScalar(drone, setDrone, key, Number(event.target.value))} /></label>)}
              <button onClick={() => setAdvanced((value) => !value)}>{advanced ? "HIDE ADVANCED" : "SHOW ADVANCED"}</button>
              {advanced && (["body_length_m", "body_width_m", "body_height_m", "arm_length_m", "payload_mass_kg", "battery_mass_kg", "max_commanded_speed_mps", "acceleration_limit_mps2"] as const).map((key) => <label key={key}>{key.replaceAll("_", " ")}<input type="number" step="0.01" value={scalar(drone, key, 0)} disabled={locked} onChange={(event) => updateScalar(drone, setDrone, key, Number(event.target.value))} /></label>)}
            </div>
            <div>
              <h3>Sensor Configuration</h3>
              <p className="provenance-note">OV9281 REFERENCE SIMULATION</p>
              <label>Width (px)<input type="number" value={Number(camera?.resolution_width ?? 1280)} disabled={locked} onChange={(event) => camera && setCamera({ ...camera, resolution_width: Number(event.target.value) })} /></label>
              <label>Height (px)<input type="number" value={Number(camera?.resolution_height ?? 800)} disabled={locked} onChange={(event) => camera && setCamera({ ...camera, resolution_height: Number(event.target.value) })} /></label>
              <label>FPS<input type="number" value={scalar(camera, "fps", 30)} disabled={locked} onChange={(event) => updateScalar(camera, setCamera, "fps", Number(event.target.value))} /></label>
              <label>Sensor Profile<select aria-label="Sensor realism profile" value={sensorProfileId} disabled={locked} onChange={(event) => setSensorProfileId(event.target.value)}><option>IDEAL_CONTROL</option><option>REFERENCE_NOMINAL</option><option>REFERENCE_CHALLENGING</option></select></label>
            </div>
          </div>
          <footer className="wizard-footer"><button className="primary" onClick={() => setWorkspaceStep(2)}>NEXT: MAP / SEARCH AREA</button></footer>
        </section>
      )}

      {(workspaceStep === 2 || workspaceStep === 3) && (
        <section className="wizard-step">
          <h2>2. Map & Search Area</h2>
          <p>Draw a search polygon on the map.</p>
          <div className="wizard-map-container">
            <SimulationWorkspaceMap mode="DRAW_POLYGON" polygon={polygon} onChangePolygon={setPolygon} survivors={[]} onPlaceSurvivor={() => {}} startPosition={null} onPlaceStart={() => {}} center={mapCenter} />
          </div>
          <div className="scenario-actions">
            <button onClick={() => setPolygon([])}>CLEAR POLYGON</button>
          </div>
          {workspaceStep === 3 && (
            <div className={`result ${polygon.length >= 3 ? 'ok' : 'error'}`}>
              {polygon.length < 3 ? "Validation Error: Search area requires at least 3 points." : "Validation OK: Search area is valid."}
            </div>
          )}
          <footer className="wizard-footer">
            <button onClick={() => setWorkspaceStep(1)}>BACK</button>
            {workspaceStep === 2 && <button className="primary" onClick={() => setWorkspaceStep(3)}>VALIDATE SEARCH REGION</button>}
            {workspaceStep === 3 && <button className="primary" disabled={polygon.length < 3} onClick={() => {
              if (polygon.length >= 3) setWorkspaceStep(4);
            }}>NEXT: ENVIRONMENT</button>}
          </footer>
        </section>
      )}

      {workspaceStep === 4 && (
        <section className="wizard-step">
          <h2>4. Environment Configuration</h2>
          <div className="wizard-content config-grid">
            <div>
              <h3>Terrain Configuration</h3>
              <p className="provenance-note">SIMULATED PROCEDURAL/FLAT TERRAIN</p>
              <label>Terrain mode<select value="FLAT" disabled><option>FLAT</option></select></label>
              <small>Real-world DEM is not currently active. Using flat simulation plane.</small>
            </div>
            <div>
              <h3>Wind & Weather</h3>
              <label>Wind mode<select value={String(environment?.wind?.mode ?? "GUSTING")} disabled={locked} onChange={(event) => environment && setEnvironment({ ...environment, wind: { ...environment.wind, mode: event.target.value } })}><option>CALM</option><option>CONSTANT</option><option>TURBULENT</option><option>GUSTING</option></select></label>
              <label>Mean speed (m/s)<input type="number" value={scalar(environment?.wind ?? null, "mean_speed_mps", 3)} disabled={locked} onChange={(event) => environment && setEnvironment({ ...environment, wind: { ...environment.wind, mean_speed_mps: { ...environment.wind.mean_speed_mps, value: Number(event.target.value) } } })} /></label>
            </div>
          </div>
          <footer className="wizard-footer">
            <button onClick={() => setWorkspaceStep(3)}>BACK</button>
            <button className="primary" onClick={() => setWorkspaceStep(5)}>NEXT: SURVIVORS</button>
          </footer>
        </section>
      )}

      {workspaceStep === 5 && (
        <section className="wizard-step">
          <h2>5. Survivor Placement</h2>
          <p className="truth-hidden">CRITICAL GROUND-TRUTH FIREWALL: Placed survivors are SCENARIO TRUTH and never exposed to the autonomy telemetry or targeting logic.</p>
          <div className="wizard-map-container">
            <SimulationWorkspaceMap mode="PLACE_SURVIVOR" polygon={polygon} onChangePolygon={setPolygon} survivors={survivors.map(s => ({ id: s.id, lng: s.longitude, lat: s.latitude }))} onPlaceSurvivor={(lng, lat) => {
              setSurvivors(current => [...current, {
                id: `survivor_${String(current.length + 1).padStart(2, "0")}`,
                eastM: 0, northM: 0, latitude: lat, longitude: lng, bodyState: current.length % 2 ? "lying" : "standing",
              }]);
            }} startPosition={startPosition} onPlaceStart={() => {}} center={mapCenter} />
          </div>
          <ol className="survivor-list">{survivors.map((item, index) => <li key={item.id} className={selectedSurvivor === item.id ? "selected" : ""}><b>{item.id}</b><span>{item.latitude.toFixed(6)}, {item.longitude.toFixed(6)}</span><button disabled={locked} aria-label={`Delete ${item.id}`} onClick={() => setSurvivors((current) => current.filter((_, i) => i !== index))}>DELETE</button></li>)}</ol>
          <div className="scenario-actions"><button onClick={() => setSurvivors([])}>CLEAR SURVIVORS</button></div>
          <footer className="wizard-footer">
            <button onClick={() => setWorkspaceStep(4)}>BACK</button>
            <button className="primary" onClick={() => setWorkspaceStep(6)}>NEXT: START POSITION</button>
          </footer>
        </section>
      )}

      {workspaceStep === 6 && (
        <section className="wizard-step">
          <h2>6. Drone Start Position</h2>
          <p>Click on the map to set the initial drone deployment location.</p>
          <div className="wizard-map-container">
            <SimulationWorkspaceMap mode="PLACE_START" polygon={polygon} onChangePolygon={setPolygon} survivors={[]} onPlaceSurvivor={() => {}} startPosition={startPosition} onPlaceStart={(lng, lat) => setStartPosition({ lng, lat })} center={mapCenter} />
          </div>
          {startPosition && <p>Start position selected: {startPosition.lat.toFixed(6)}, {startPosition.lng.toFixed(6)}</p>}
          <footer className="wizard-footer">
            <button onClick={() => setWorkspaceStep(5)}>BACK</button>
            <button className="primary" onClick={() => setWorkspaceStep(7)}>NEXT: REVIEW</button>
          </footer>
        </section>
      )}

      {workspaceStep === 7 && (
        <section className="wizard-step">
          <h2>7. Review Mission Configuration</h2>
          <div className="review-grid">
            <div>
              <h3>AUTONOMY CONFIGURATION</h3>
              <dl>
                <dt>Drone Layout</dt><dd>{drone?.rotor_layout}</dd>
                <dt>Camera</dt><dd>{camera?.resolution_width}x{camera?.resolution_height} @ {scalar(camera, "fps", 30)}fps</dd>
                <dt>Search Area</dt><dd>{polygon.length} vertices</dd>
                <dt>Sensor Profile</dt><dd>{sensorProfileId}</dd>
              </dl>
            </div>
            <div>
              <h3>SCENARIO (GROUND TRUTH)</h3>
              <dl>
                <dt>Terrain Mode</dt><dd>FLAT</dd>
                <dt>Wind</dt><dd>{environment?.wind?.mode} ({scalar(environment?.wind ?? null, "mean_speed_mps", 0)} m/s)</dd>
                <dt>Hidden Survivors</dt><dd>{survivors.length}</dd>
                <dt>Start Position</dt><dd>{startPosition ? "Selected" : "Default"}</dd>
              </dl>
            </div>
          </div>
          <footer className="wizard-footer">
            <button onClick={() => setWorkspaceStep(6)}>EDIT CONFIGURATION</button>
            <button className="primary" onClick={() => setWorkspaceStep(8)}>APPROVE & PREPARE</button>
          </footer>
        </section>
      )}

      {workspaceStep === 8 && (
        <section className="wizard-step">
          <h2>8. Prepare & Start Simulation</h2>
          <p>Click <b>GENERATE WORLD</b> to compile the Gazebo world. Any existing simulation is stopped automatically.</p>
          <div className="run-controls">
            <b>{String(simStatus.state)}</b>
            <p>{message}</p>
            <div style={{ display: "flex", gap: "10px", marginTop: "1rem" }}>
              <button className="primary" onClick={prepare}>GENERATE WORLD</button>
              {simStatus.state === "READY" && <button className="primary success" onClick={bootEnvironment}>PREPARE LAUNCH ENVIRONMENT</button>}
              {simStatus.state === "RUNNING" && <button className="primary success" onClick={goToMissionTab}>GO TO MISSION TAB</button>}
              {(simStatus.state === "RUNNING" || simStatus.state === "STARTING_GAZEBO" || simStatus.state === "WAITING_FOR_READY") && <button className="abort-button" onClick={stop}>STOP SIMULATION</button>}
            </div>
          </div>
        </section>
      )}
    </div>
  </main>;
}
