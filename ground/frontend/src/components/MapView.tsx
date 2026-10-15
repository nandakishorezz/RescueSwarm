import { useMemo } from "react";
import { useGcsStore } from "../stores/gcsStore";

export function MapView() {
  const mission = useGcsStore((state) => state.mission);
  const vehicles = useGcsStore((state) => state.vehicles);
  const trails = useGcsStore((state) => state.trails);
  const targets = useGcsStore((state) => state.targets);
  const mapError = useGcsStore((state) => state.error);
  const mode = useGcsStore((state) => state.mode);
  const layers = useGcsStore((state) => state.digitalTwinLayers);

  // Read static scenario from localStorage
  const rawScenario = localStorage.getItem("rescueswarm.simulationScenario");
  const scenario = useMemo(() => {
    try { return rawScenario ? JSON.parse(rawScenario) : null; } catch { return null; }
  }, [rawScenario]);

  // Gather coordinates to compute bounding box
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  const extendBounds = (lon: number, lat: number) => {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  };

  const poly: [number, number][] = scenario?.polygon || [];
  const survivors = scenario?.survivors || [];
  const autonomousSurvivors = Object.values(targets).filter(
    (target) => target.state !== "REJECTED" && target.state !== "CANDIDATE"
  );
  const startPos = scenario?.startPosition;

  poly.forEach((p) => extendBounds(p[0], p[1]));
  survivors.forEach((s: any) => extendBounds(s.longitude, s.latitude));
  autonomousSurvivors.forEach((target) => extendBounds(target.longitude, target.latitude));
  if (startPos) extendBounds(startPos.lng, startPos.lat);

  // Include live drone positions so the map expands if it flies outside
  Object.values(vehicles).forEach((v) => {
    if (v.latitude && v.longitude) extendBounds(v.longitude, v.latitude);
  });

  if (minLon === Infinity) {
    minLon = 77.591; maxLon = 77.595; minLat = 12.971; maxLat = 12.975;
  }

  // Projection requested by user
  const width = 1000;
  const height = 600;
  const padding = 50;

  const lonRange = maxLon - minLon || 0.001;
  const latRange = maxLat - minLat || 0.001;

  const toX = (lon: number) => padding + ((lon - minLon) / lonRange) * (width - 2 * padding);
  const toY = (lat: number) => padding + ((maxLat - lat) / latRange) * (height - 2 * padding);

  return (
    <div className="map-shell" style={{ position: "relative", width: "100%", height: "100%", minHeight: "500px", backgroundColor: "#0b1120", overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div data-testid="svg-runtime-test-999" style={{background: 'red', color: 'white', fontSize: '32px', zIndex: 999999, position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)'}}>RESCUESWARM SVG RUNTIME TEST 999</div>
      <svg width="100%" height="100%" viewBox="0 0 1000 600" preserveAspectRatio="none" style={{ display: "block" }}>
        
        {/* Background Grid */}
        {Array.from({ length: 9 }).map((_, i) => (
          <line key={`hx-${i}`} x1="0" y1={(i + 1) * 60} x2="1000" y2={(i + 1) * 60} stroke="#ffffff" strokeOpacity="0.03" strokeWidth="1" />
        ))}
        {Array.from({ length: 9 }).map((_, i) => (
          <line key={`vx-${i}`} x1={(i + 1) * 100} y1="0" x2={(i + 1) * 100} y2="600" stroke="#ffffff" strokeOpacity="0.03" strokeWidth="1" />
        ))}

        {/* SEARCH POLYGON */}
        {poly.length >= 3 && (
          <polygon
            points={poly.map((p) => `${toX(p[0])},${toY(p[1])}`).join(" ")}
            fill="rgba(52, 211, 153, 0.1)" stroke="#34d399" strokeWidth="2" strokeDasharray="4 4"
          />
        )}

        {/* DRONE TRAILS */}
        {layers?.paths !== false && Object.entries(trails).map(([id, points]) => {
          if (points.length < 2) return null;
          return (
            <polyline
              key={`trail-${id}`}
              points={points.map(p => `${toX(p[0])},${toY(p[1])}`).join(" ")}
              fill="none" stroke="#f8c15c" strokeWidth="3" opacity="0.8"
            />
          );
        })}

        {/* SURVIVORS */}
        {survivors.map((s: any, i: number) => (
          <text key={`surv-${i}`} x={toX(s.longitude)} y={toY(s.latitude)} fontSize="24" textAnchor="middle" dominantBaseline="middle">
            🧍
          </text>
        ))}

        {/* Pixel-derived, mission-scoped survivor records.  These are separate
            from the static scenario-truth markers above and keyed by stable ID. */}
        {layers?.targets !== false && autonomousSurvivors.map((target) => (
          <g key={`autonomous-survivor-${target.target_id}`} data-testid="autonomous-survivor-marker"
            transform={`translate(${toX(target.longitude)}, ${toY(target.latitude)})`}>
            <circle r="13" fill="#14532d" stroke="#4ade80" strokeWidth="2" />
            <text y="6" fontSize="16" textAnchor="middle">🧍</text>
            <text y="-18" fontSize="11" fill="#bbf7d0" textAnchor="middle" fontWeight="bold">
              {target.target_id.slice(0, 8)}
            </text>
          </g>
        ))}

        {/* DRONE START */}
        {startPos && (
          <g transform={`translate(${toX(startPos.lng)}, ${toY(startPos.lat)})`}>
            <text x="0" y="-10" fontSize="32" textAnchor="middle" dominantBaseline="middle" opacity="0.5">🚁</text>
            <text x="0" y="15" fontSize="12" fill="#94a3b8" textAnchor="middle" fontWeight="bold">START</text>
          </g>
        )}

        {/* LIVE DRONE */}
        {Object.values(vehicles).map((v) => {
          if (!v.latitude || !v.longitude) return null;
          return (
            <g key={`live-${v.vehicle_id}`} transform={`translate(${toX(v.longitude)}, ${toY(v.latitude)})`}>
              <text x="0" y="-10" fontSize="36" textAnchor="middle" dominantBaseline="middle">🚁</text>
              <text x="0" y="20" fontSize="14" fill="#fff" textAnchor="middle" fontWeight="bold" stroke="#34d399" strokeWidth="1" paintOrder="stroke">LIVE</text>
            </g>
          );
        })}
      </svg>
      {mapError && <div className="map-error" role="alert">MAP UNAVAILABLE · {mapError}</div>}
    </div>
  );
}
