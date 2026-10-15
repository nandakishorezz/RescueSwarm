import { useGcsStore } from "../stores/gcsStore";

export function FleetPanel() {
  const fleet = useGcsStore((state) => state.fleetStatus);
  const vehicles = useGcsStore((state) => state.vehicles);
  if (!fleet || fleet.fleet_size < 2) return null;
  const active = Object.values(vehicles).filter((vehicle) => vehicle.connected).length;
  const coverage = fleet.progress?.combined_coverage_percent;
  return (
    <section className="panel fleet-panel" aria-label="Fleet mission status">
      <div className="panel-title"><h2>FLEET MISSION</h2><span className="sim-tag">MULTI-SITL</span></div>
      <div className="metric-grid">
        <div><span>State</span><strong>{fleet.state}</strong></div>
        <div><span>Active</span><strong>{active} / {fleet.fleet_size}</strong></div>
        <div><span>Combined coverage</span><strong>{coverage == null ? "—" : `${coverage.toFixed(1)}%`}</strong></div>
        <div><span>Partition imbalance</span><strong>{fleet.partition?.imbalance_percent == null ? "—" : `${fleet.partition.imbalance_percent.toFixed(1)}%`}</strong></div>
      </div>
      <small>ONE GCS · ONE MISSION START · H1 {fleet.hardware_gate_h1}</small>
    </section>
  );
}
