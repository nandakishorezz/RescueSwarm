import { useMemo, useState } from "react";

import { useGcsStore } from "../stores/gcsStore";

export function EventFeed() {
  const source = useGcsStore((state) => state.events);
  const [vehicle, setVehicle] = useState("ALL"); const [severity, setSeverity] = useState("ALL"); const [family, setFamily] = useState("ALL");
  const vehicles = useMemo(() => [...new Set(source.map((event) => event.vehicle_id).filter(Boolean))] as string[], [source]);
  const families = useMemo(() => [...new Set(source.map((event) => event.type.split("_")[0]))].sort(), [source]);
  const events = useMemo(() => source.filter((event) =>
    (vehicle === "ALL" || event.vehicle_id === vehicle) &&
    (severity === "ALL" || event.severity === severity) &&
    (family === "ALL" || event.type.startsWith(`${family}_`))
  ).slice(-12).reverse(), [source, vehicle, severity, family]);
  return (
    <section className="panel event-panel">
      <div className="panel-title"><h2>MISSION TIMELINE</h2><span>{events.length} SHOWN</span></div>
      <div className="event-filters" aria-label="Mission timeline filters">
        <label>Vehicle<select value={vehicle} onChange={(event) => setVehicle(event.target.value)}><option>ALL</option>{vehicles.map((id) => <option key={id}>{id}</option>)}</select></label>
        <label>Severity<select value={severity} onChange={(event) => setSeverity(event.target.value)}><option>ALL</option>{["INFO", "WARNING", "CRITICAL", "ERROR"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Family<select value={family} onChange={(event) => setFamily(event.target.value)}><option>ALL</option>{families.map((value) => <option key={value}>{value}</option>)}</select></label>
      </div>
      {!events.length && <p className="empty">NO EVENTS MATCH THE CURRENT FILTERS</p>}
      <ol>{events.map((event) => (
        <li className={`severity-${event.severity.toLowerCase()}`} key={event.event_id}>
          <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
          <div><strong>{event.type.replaceAll("_", " ")}</strong><span>{event.message}</span></div>
        </li>
      ))}</ol>
    </section>
  );
}
