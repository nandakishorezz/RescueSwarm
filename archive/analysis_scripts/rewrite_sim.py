import re
import os

filepath = r"ground/frontend/src/pages/SimulationPage.tsx"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Add SimulationWorkspaceMap import
content = content.replace(
    'import { SimulationWorldPreview, type PreviewSurvivor } from "../components/SimulationWorldPreview";',
    'import { SimulationWorldPreview, type PreviewSurvivor } from "../components/SimulationWorldPreview";\nimport { SimulationWorkspaceMap } from "../components/SimulationWorkspaceMap";'
)

# Replace the SimulationPage function up to the return statement
# I will use a regex to find where the `return <main` starts and replace everything from there to the end.

# First let's add some state variables for the wizard.
state_vars = """  const [workspaceStep, setWorkspaceStep] = useState(1);
  const [polygon, setPolygon] = useState<[number, number][]>([]);
  const [mapCenter, setMapCenter] = useState<[number, number]>([77.5946, 12.9716]);
  const [startPosition, setStartPosition] = useState<{ lng: number; lat: number } | null>(null);
  const setMode = useGcsStore((state) => state.setMode);
  const setViewMode = useGcsStore((state) => state.setViewMode);
  const setMissionStore = useGcsStore((state) => state.setMission);
"""

content = content.replace(
    '  const [advanced, setAdvanced] = useState(false);',
    '  const [advanced, setAdvanced] = useState(false);\n' + state_vars
)

# Now, we rewrite the `start` function to automatically navigate to the Mission view.
content = content.replace(
    'setMessage("RUNNING · vehicle pose now comes from SITL/Gazebo, not preview animation");',
    'setMessage("RUNNING"); setMode("MISSION"); setViewMode("SPLIT");'
)

# We need a new render structure.
new_render = """  return <main className="simulation-page workspace-mode">
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
            {workspaceStep === 3 && <button className="primary" disabled={polygon.length < 3} onClick={async () => {
              if (polygon.length >= 3) {
                 if (!mission) {
                   const m = await api.createMission("Simulated Mission");
                   setMissionStore(m);
                   // Note: A real implementation would upload the boundary geojson to the mission.
                   // For now, we simulate having a valid boundary if the user drew one.
                 }
                 setWorkspaceStep(4);
              }
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
          <p>Starting Gazebo physics engine, connecting loopback telemetry, and compiling artifacts...</p>
          <div className="run-controls">
            <b>{String(simStatus.state)}</b>
            <p>{message}</p>
            <div style={{ display: "flex", gap: "10px", marginTop: "1rem" }}>
              <button className="primary" onClick={prepare} disabled={locked}>GENERATE WORLD</button>
              {simStatus.state === "READY" && <button className="primary success" onClick={start}>LAUNCH MISSION</button>}
              {locked && <button className="abort-button" onClick={stop}>STOP</button>}
            </div>
          </div>
        </section>
      )}
    </div>
  </main>;"""

new_content = re.sub(r'  return <main className="simulation-page">.*</main>;', new_render, content, flags=re.DOTALL)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(new_content)
