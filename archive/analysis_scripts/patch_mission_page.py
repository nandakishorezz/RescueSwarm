import re

filepath = r"ground/frontend/src/pages/MissionPage.tsx"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Add useState and useEffect imports if they aren't there
if "useState" not in content:
    content = content.replace('import { api }', 'import { useEffect, useState } from "react";\nimport { api }')

# Add eval state and observerData
state_add = """  const [evalMode, setEvalMode] = useState(false);
  const [observerData, setObserverData] = useState<any>(null);

  useEffect(() => {
    if (!evalMode || !["RUNNING", "COMPLETED", "MISSION COMPLETE"].includes(missionState ?? "")) return;
    const fetchTruth = async () => {
      try {
        const [truth, metrics] = await Promise.all([api.simulationObserverTruth(), api.simulationPerceptionObserver()]);
        setObserverData({ truth, metrics });
      } catch (e) {
        console.error(e);
      }
    };
    fetchTruth();
    const timer = setInterval(fetchTruth, 5000);
    return () => clearInterval(timer);
  }, [evalMode, missionState]);
"""

content = content.replace('  const action = async', state_add + '\n  const action = async')

# Modify command bar to include the toggle
toggle_html = """        <div className="competition-actions">
          <label className="eval-toggle"><input type="checkbox" checked={evalMode} onChange={(e) => setEvalMode(e.target.checked)} /> <b>{evalMode ? 'EVALUATION MODE' : 'OPERATOR MODE'}</b></label>
          <button className="abort-button" disabled={!mission || !["RUNNING", "RETURNING"].includes(mission.state)} onClick={() => action("SAFETY_ABORT")}>SAFETY ABORT</button>
          <button className="recall-button" disabled={!mission || !["RUNNING", "RETURNING"].includes(mission.state)} onClick={() => action("EMERGENCY_RECALL")}>EMERGENCY RECALL</button>
        </div>"""

content = re.sub(r'<div className="competition-actions">.*?</div>', toggle_html, content, flags=re.DOTALL)

# Add Results panel if completed
results_panel = """      {evalMode && observerData && (
        <aside className="evaluation-panel" style={{ background: '#1e293b', padding: '10px', color: '#fff', fontSize: '0.85rem' }}>
          <h3>EVALUATION MODE (TRUTH ACTIVE)</h3>
          <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px' }}>
             <dt>Visible frames</dt><dd>{observerData?.metrics?.summary?.truth_visible_person_frames ?? "—"}</dd>
             <dt>Best error</dt><dd>{observerData?.metrics?.summary?.best_geolocation_error_m?.toFixed?.(2) ?? "—"} m</dd>
             <dt>Proof source</dt><dd>{observerData?.metrics?.prediction_vs_truth?.proof_source ?? "—"}</dd>
          </dl>
          {["MISSION COMPLETE", "COMPLETED"].includes(missionState ?? "") && (
            <div style={{ marginTop: '10px', borderTop: '1px solid #334155', paddingTop: '10px' }}>
              <h4>POST-MISSION RESULTS</h4>
              <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px' }}>
                <dt>Search Area</dt><dd>{mission?.boundary?.area_hectares.toFixed(2) ?? 0} ha</dd>
                <dt>Mission Duration</dt><dd>Available in logs</dd>
                <dt>Missed Detections</dt><dd>{observerData?.metrics?.prediction_vs_truth?.missed_detections ?? 0}</dd>
                <dt>False Detections</dt><dd>{observerData?.metrics?.prediction_vs_truth?.false_detections ?? 0}</dd>
              </dl>
            </div>
          )}
        </aside>
      )}
"""

content = content.replace('      <div className="map-region">', results_panel + '\n      <div className="map-region">')

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
