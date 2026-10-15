import { useGcsStore } from "../stores/gcsStore";
import { AttitudeIndicator } from "./AttitudeIndicator";

export function VehiclePanel() {
  const vehicles = useGcsStore((state) => state.vehicles);
  const source = useGcsStore((state) => state.executionSource);
  const selected = useGcsStore((state) => state.selectedVehicleId);
  const select = useGcsStore((state) => state.selectVehicle);
  const values = Object.values(vehicles).sort((left, right) => left.vehicle_id.localeCompare(right.vehicle_id));
  if (!values.length) return <section className="panel vehicle-panel"><h2>FLEET VEHICLES</h2><p className="empty">VEHICLE SOURCE NOT CONNECTED</p></section>;
  return (
    <section className="panel vehicle-panel">
      <div className="panel-title"><h2>FLEET VEHICLES</h2><span className="sim-tag">{source === "SITL" ? "ARDUPILOT SITL" : source}</span></div>
      {values.map((vehicle) => { const ageSeconds = Math.max(0, (Date.now() - new Date(vehicle.last_update).getTime()) / 1000); return <article role="button" tabIndex={0} onClick={() => select(vehicle.vehicle_id)} onKeyDown={(event) => event.key === "Enter" && select(vehicle.vehicle_id)} className={`fleet-vehicle ${selected === vehicle.vehicle_id ? "selected" : ""}`} key={vehicle.vehicle_id}>
        <b>{vehicle.vehicle_id.replace("_", " ").toUpperCase()}</b><span>{vehicle.flight_mode}</span>
        <small>{vehicle.linkState === "STALE" ? `STALE — ${ageSeconds.toFixed(1)} s` : vehicle.linkState ?? "CONNECTED"} · BAT {vehicle.battery_percent.toFixed(0)}% · ALT {vehicle.relative_altitude_m.toFixed(1)} m</small>
        <small>{vehicle.current_task} · WP <span className="mission-sequence">{`${vehicle.current_mission_item ?? "—"} / ${vehicle.total_mission_items ?? "—"}`}</span></small>
        {selected === vehicle.vehicle_id && <div className="vehicle-detail">
          <AttitudeIndicator rollDeg={vehicle.roll_deg} pitchDeg={vehicle.pitch_deg} headingDeg={vehicle.yaw_deg} />
          <dl><dt>Latitude</dt><dd>{vehicle.latitude.toFixed(7)}</dd><dt>Longitude</dt><dd>{vehicle.longitude.toFixed(7)}</dd>
            <dt>Relative altitude</dt><dd>{vehicle.relative_altitude_m.toFixed(1)} m</dd><dt>Absolute altitude</dt><dd>{vehicle.absolute_altitude_m?.toFixed(1) ?? "—"}</dd>
            <dt>AGL estimate</dt><dd>{vehicle.agl_altitude_m?.toFixed(1) ?? "—"}</dd><dt>Ground speed</dt><dd>{vehicle.ground_speed_m_s.toFixed(1)} m/s</dd>
            <dt>Vertical speed</dt><dd>{vehicle.vertical_speed_m_s?.toFixed(1) ?? "—"} m/s</dd><dt><abbr title="Global Positioning System">GPS</abbr></dt><dd>FIX {vehicle.gps_fix_type} · {vehicle.satellites} SAT</dd></dl>
        </div>}
      </article>; })}
    </section>
  );
}
