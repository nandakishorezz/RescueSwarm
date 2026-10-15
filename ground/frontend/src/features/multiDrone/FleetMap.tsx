import type { FleetVehicleView } from "./types";

export type FleetTrails = Record<string, [number, number][]>;

export function appendFleetTrails(
  previous: FleetTrails, vehicles: FleetVehicleView[],
): FleetTrails {
  const next = { ...(previous ?? {}) };
  for (const vehicle of vehicles ?? []) {
    const point = vehicle?.telemetry;
    if (!point || !Number.isFinite(point.latitude) || !Number.isFinite(point.longitude)
        || (point.latitude === 0 && point.longitude === 0)) continue;
    const trail = next[vehicle.vehicle_id] ?? [];
    const last = trail[trail.length - 1];
    if (!last || Math.abs(last[0] - point.longitude) + Math.abs(last[1] - point.latitude) > 1e-8) {
      next[vehicle.vehicle_id] = [...trail.slice(-999), [point.longitude, point.latitude]];
    }
  }
  return next;
}

export function FleetMap({ vehicles, trails }: { vehicles: FleetVehicleView[]; trails: FleetTrails }) {
  const safeTrails = trails ?? {};
  const points = Object.values(safeTrails)
    .flat()
    .filter((p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]));

  if (!points.length) return <div className="fleet-map-empty">Map waiting for real fleet GPS telemetry</div>;

  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const centerLon = (minLon + maxLon) / 2;
  const centerLat = (minLat + maxLat) / 2;
  const scale = 111_320;
  const cosLat = Math.cos((centerLat * Math.PI) / 180);
  const xy = ([lon, lat]: [number, number]): [number, number] => [
    300 + (lon - centerLon) * scale * cosLat * 4,
    180 - (lat - centerLat) * scale * 4,
  ];
  const colors = ["#38bdf8", "#f59e0b", "#a78bfa", "#34d399"];

  return (
    <svg role="img" aria-label="Live fleet GPS trails" viewBox="0 0 600 360" className="fleet-map">
      <rect width="600" height="360" fill="#0b1925" />
      {Array.from({ length: 11 }, (_, i) => (
        <g key={i} stroke="#1d3444" strokeWidth="1">
          <line x1={i * 60} x2={i * 60} y1="0" y2="360" />
          <line x1="0" x2="600" y1={i * 36} y2={i * 36} />
        </g>
      ))}
      {(vehicles ?? []).map((vehicle, index) => {
        const trail = safeTrails[vehicle.vehicle_id] ?? [];
        const validPoints = trail.filter((p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]));
        const last = validPoints[validPoints.length - 1];
        const color = colors[index % colors.length];
        return (
          <g key={vehicle.vehicle_id}>
            {validPoints.length > 1 && (
              <polyline
                fill="none"
                stroke={color}
                strokeWidth="2"
                points={validPoints.map((p) => xy(p).join(",")).join(" ")}
              />
            )}
            {last && (
              <>
                <circle cx={xy(last)[0]} cy={xy(last)[1]} r="7" fill={color} stroke="#fff" strokeWidth="2" />
                <text x={xy(last)[0] + 10} y={xy(last)[1] - 9} fill={color} fontSize="13">
                  {vehicle.vehicle_id}
                </text>
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}
