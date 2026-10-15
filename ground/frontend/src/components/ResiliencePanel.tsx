import { useEffect, useState } from "react";

import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

interface Scenario {
  scenario_id: string;
  description: string;
  critical: boolean;
  phase: string;
}

export function ResiliencePanel() {
  const competition = useGcsStore((state) => state.competitionMode);
  const status = useGcsStore((state) => state.resilienceStatus);
  const setStatus = useGcsStore((state) => state.setResilienceStatus);
  const setError = useGcsStore((state) => state.setError);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    api.resilienceStatus().then((value) => setStatus(value as never)).catch(() => undefined);
    if (!competition) api.resilienceScenarios().then((items) => {
      const loaded = items as unknown as Scenario[];
      setScenarios(loaded);
      setSelected((current) => current || loaded[0]?.scenario_id || "");
    }).catch(() => setScenarios([]));
  }, [competition, setStatus]);

  const run = async () => {
    if (!selected) return;
    try {
      const output = await api.runFailureScenario(selected);
      setResult(output);
      setError(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "FAILURE SCENARIO FAILED");
    }
  };

  if (competition) return null;
  return (
    <section className="resilience-block">
      <div className="panel-title"><h2>RESILIENCE LAB</h2><span className="sim-tag">SITL / ENGINEERING ONLY</span></div>
      <p>Deterministic fault injection. This API is disabled in Competition Mode and cannot command physical endpoints.</p>
      <div className="health-summary">
        <span>System <b>{status?.aggregate_health ?? "NOT_READY"}</b></span>
        <span>Failsafe owner <b>{status?.safety_decision?.authoritative_layer ?? "NONE"}</b></span>
        <span>Action <b>{status?.safety_decision?.recommended_action ?? "CONTINUE"}</b></span>
        <span>H1 <b>{status?.hardware_gate_h1.state ?? "PENDING"}</b></span>
      </div>
      <label>Failure scenario<select value={selected} onChange={(event) => setSelected(event.target.value)}>
        {scenarios.map((scenario) => <option key={scenario.scenario_id} value={scenario.scenario_id}>{scenario.critical ? "CRITICAL · " : ""}{scenario.scenario_id}</option>)}
      </select></label>
      <button disabled={!selected} onClick={run}>RUN NORMALIZED FAULT SCENARIO</button>
      {result && <pre>{JSON.stringify(result, null, 2)}</pre>}
    </section>
  );
}
