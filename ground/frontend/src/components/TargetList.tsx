import { useGcsStore } from "../stores/gcsStore";
import { CopyButton } from "./CopyButton";

export function TargetList() {
  const targetStates = useGcsStore((state) => state.targets);
  const targets = Object.values(targetStates).filter((target) => target.state !== "REJECTED" && target.state !== "CANDIDATE");
  const selected = useGcsStore((state) => state.selectedTargetId);
  const select = useGcsStore((state) => state.selectTarget);
  return (
    <section className="panel target-panel">
      <div className="panel-title"><h2>SURVIVORS</h2><span>{targets.length}</span></div>
      {!targets.length && <p className="empty">NO TARGET OBSERVATIONS</p>}
      <div className="target-list">
        {targets.map((target, index) => (
          <article role="button" tabIndex={0} onClick={() => select(target.target_id)} onKeyDown={(event) => event.key === "Enter" && select(target.target_id)} className={`target target-${target.state.toLowerCase()} ${selected === target.target_id ? "selected" : ""}`} key={target.target_id}>
            <div><strong>#{String(index + 1).padStart(2, "0")}</strong>{target.simulated && <span className="sim-tag">SIM</span>}</div>
            <b>{target.state}</b>
            <small>ID {target.target_id.slice(0, 8)}</small>
            <small>Detection confidence {(target.detection_confidence * 100).toFixed(0)}%</small>
            <small>Position uncertainty ±{target.geolocation_uncertainty_m.toFixed(1)} m · {target.observation_count} observations</small>
            <small>{target.latitude.toFixed(7)}, {target.longitude.toFixed(7)}</small>
            <small>Location: {target.geolocation_quality === "PRECISION" || target.geolocation_quality === "GOOD" ? "RELIABLE" : "APPROXIMATE"}</small>
            {target.geolocation_quality && <small>Quality: {target.geolocation_quality}</small>}
            <small>Last seen {new Date(target.last_seen_at).toLocaleTimeString()}</small>
            {target.observing_vehicle_ids?.length && <small>Observed by: {target.observing_vehicle_ids.join(", ")}</small>}
            {target.assigned_vehicle_id && <small>Assigned delivery: {target.assigned_vehicle_id}</small>}
            <small>Delivery: {target.delivery_state}</small>
            {selected === target.target_id && <div className="target-drawer"><div className="copy-row"><b title={target.target_id}>Target {target.target_id}</b><CopyButton value={target.target_id} label="Copy target ID" /></div><b>Evidence & timeline</b><span>First {new Date(target.first_seen_at).toLocaleTimeString()}</span><span>Last {new Date(target.last_seen_at).toLocaleTimeString()}</span><span>ENU E {target.east_m.toFixed(2)} m · N {target.north_m.toFixed(2)} m</span><span>Inliers {target.inlier_count ?? "—"} · Outliers {target.outlier_count ?? "—"}</span><span>Evidence {target.evidence_references?.join(", ") || "NONE"}</span></div>}
          </article>
        ))}
      </div>
    </section>
  );
}
