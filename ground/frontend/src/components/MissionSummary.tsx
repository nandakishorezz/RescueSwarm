import { useGcsStore } from "../stores/gcsStore";

export function MissionSummary() {
  const mission = useGcsStore((state) => state.mission);
  const source = useGcsStore((state) => state.executionSource);
  const rescueState = useGcsStore((state) => state.rescueState);
  const payloadRemaining = useGcsStore((state) => state.payloadRemaining);
  return (
    <section className="panel mission-summary">
      <div className="panel-title"><h2>SEARCH</h2><strong>{mission?.coverage_percent.toFixed(0) ?? 0}%</strong></div>
      <div className="progress"><i style={{ width: `${mission?.coverage_percent ?? 0}%` }} /></div>
      <div className="mission-meta">
        <span>{mission?.name ?? "NO MISSION"}</span>
        <b>{mission?.state ?? "DRAFT"}</b>
        {source === "SITL" && <b>RESCUE: {rescueState.replaceAll("_", " ")}</b>}
        {payloadRemaining !== null && <small>Payloads remaining: {payloadRemaining}</small>}
        <small>{source === "SITL" ? "Coverage measured from the actual SITL search trail." : "Coverage shown is Stage 2 GCS simulation geometry."}</small>
      </div>
    </section>
  );
}
