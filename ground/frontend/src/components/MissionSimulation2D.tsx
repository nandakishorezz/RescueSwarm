import React, { useEffect, useState, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { setupStyle } from "./mapStyle";
import { useVisualizationModel } from "./useVisualizationModel";
import { useGcsStore } from "../stores/gcsStore";
import type { Target, VehicleState } from "../types/domain";

export function canonicalSurvivorFeatures(targets: Record<string, Target>) {
  return {
    type: "FeatureCollection" as const,
    features: Object.values(targets)
      .filter((target) => target.state !== "REJECTED" && target.state !== "CANDIDATE")
      .map((target) => {
        let deliverySuffix = "";
        let color = "#0891b2";
        let strokeColor = "#67e8f9";
        if (target.delivery_state === "DELIVERED_VERIFIED" || target.delivery_state === "DELIVERED") {
          deliverySuffix = " ✓";
          color = "#059669";
          strokeColor = "#34d399";
        } else if (target.delivery_state === "APPROACHING") {
          deliverySuffix = " 🛬";
          color = "#d97706";
          strokeColor = "#fcd34d";
        } else if (target.delivery_state === "DELIVERY_QUEUED") {
          deliverySuffix = " ⏱";
          color = "#0284c7";
          strokeColor = "#7dd3fc";
        } else if (target.delivery_state === "FAILED") {
          deliverySuffix = " ✖";
          color = "#dc2626";
          strokeColor = "#fca5a5";
        } else if (target.delivery_state === "DEFERRED") {
          deliverySuffix = " ⏸";
          color = "#ea580c";
          strokeColor = "#fdba74";
        }

        return {
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [target.longitude, target.latitude] },
          properties: {
            target_id: target.target_id,
            label: `${target.target_id.slice(0, 8)}${deliverySuffix}`,
            delivery_state: target.delivery_state,
            circle_color: color,
            stroke_color: strokeColor,
          },
        };
      }),
  };
}

export function MissionSimulation2D({ scenario }: { scenario?: any }) {
  const [now, setNow] = useState(performance.now());
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const droneMarkerRef = useRef<maplibregl.Marker | null>(null);
  const targets = useGcsStore((state) => state.targets);
  const targetsRef = useRef(targets);
  targetsRef.current = targets;
  
  // Throttle the 'now' state update for telemetry freshness to 1Hz
  // This preserves the paint-invalidation fix by completely bypassing React state for 60Hz telemetry
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const model = useVisualizationModel(scenario, now);
  const telemetryBufferRef = useRef<VehicleState[]>([]);
  
  // Buffer telemetry
  useEffect(() => {
    if (model.liveVehicle && model.liveVehicle._receive_time_ms) {
      const buffer = telemetryBufferRef.current;
      if (buffer.length === 0 || buffer[buffer.length - 1].sequence !== model.liveVehicle.sequence) {
        buffer.push(model.liveVehicle);
        if (buffer.length > 20) buffer.shift();
      }
    }
  }, [model.liveVehicle]);

  // Map initialization
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    
    // Compute initial bounds from model to center the map
    let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
    const extend = (pt: { lat: number; lon: number }) => {
      if (pt.lat < minLat) minLat = pt.lat;
      if (pt.lat > maxLat) maxLat = pt.lat;
      if (pt.lon < minLon) minLon = pt.lon;
      if (pt.lon > maxLon) maxLon = pt.lon;
    };
    model.polyPoints.forEach(extend);
    if (minLat === Infinity) { minLat = 12.9716; maxLat = 12.9716; minLon = 77.5946; maxLon = 77.5946; }
    
    const center: [number, number] = [(minLon + maxLon)/2, (minLat + maxLat)/2];
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: setupStyle,
      center,
      zoom: 16,
      attributionControl: false
    });
    mapRef.current = map;
    
    map.on('style.load', () => {
      // Add polygon
      map.addSource('polygon', {
        type: 'geojson',
        data: model.polyPoints.length > 2 ? {
          type: 'Feature',
          geometry: {
            type: 'Polygon',
            coordinates: [[...model.polyPoints.map(p => [p.lon, p.lat]), [model.polyPoints[0].lon, model.polyPoints[0].lat]]]
          },
          properties: {}
        } : { type: 'FeatureCollection', features: [] }
      });
      map.addLayer({ id: 'polygon-fill', type: 'fill', source: 'polygon', paint: { 'fill-color': '#34d399', 'fill-opacity': 0.2 } });
      map.addLayer({ id: 'polygon-line', type: 'line', source: 'polygon', paint: { 'line-color': '#54e7b1', 'line-width': 2 } });
      
      // Add survivors (Emojis)
      map.addSource('survivors', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: model.survivors.map(s => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
            properties: { id: s.id }
          }))
        }
      });
      map.addLayer({
        id: 'survivor-markers', type: 'symbol', source: 'survivors',
        layout: { 'text-field': '🧍', 'text-size': 18, 'text-offset': [0, -0.2] },
      });

      // Autonomous predictions are a separate, canonical-ID-keyed layer.
      // Scenario actors above remain evaluation truth, never detection input.
      map.addSource('autonomous-survivors', {
        type: 'geojson', data: canonicalSurvivorFeatures(targetsRef.current),
      });
      map.addLayer({
        id: 'autonomous-survivor-circles', type: 'circle', source: 'autonomous-survivors',
        paint: {
          'circle-radius': 9,
          'circle-color': ['coalesce', ['get', 'circle_color'], '#0891b2'],
          'circle-stroke-width': 2,
          'circle-stroke-color': ['coalesce', ['get', 'stroke_color'], '#67e8f9']
        },
      });
      map.addLayer({
        id: 'autonomous-survivor-labels', type: 'symbol', source: 'autonomous-survivors',
        layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, -1.5] },
        paint: { 'text-color': '#67e8f9', 'text-halo-color': '#071827', 'text-halo-width': 1 },
      });

      // Add start pos
      map.addSource('start-pos', {
        type: 'geojson',
        data: model.startPos ? {
          type: 'FeatureCollection',
          features: [{
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [model.startPos.lon, model.startPos.lat] },
            properties: {}
          }]
        } : { type: 'FeatureCollection', features: [] }
      });
      map.addLayer({
        id: 'start-marker', type: 'symbol', source: 'start-pos',
        layout: { 'text-field': '🚁', 'text-size': 22 },
      });

      // Add trail
      map.addSource('trail', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] }, properties: {} } });
      map.addLayer({ id: 'trail-line', type: 'line', source: 'trail', paint: { 'line-color': 'cyan', 'line-width': 2 } });

      // Create Drone DOM element
      const el = document.createElement('div');
      el.className = 'live-drone-marker';
      el.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="white" style="filter: drop-shadow(0 0 2px black);"><path d="M12 2L22 20L12 16L2 20L12 2Z" /></svg>`;
      el.style.transformOrigin = "center center";
      
      const marker = new maplibregl.Marker({ element: el, rotationAlignment: 'map', pitchAlignment: 'map' })
        .setLngLat(center)
        .addTo(map);
        
      droneMarkerRef.current = marker;
      
      // Fit bounds safely
      if (model.polyPoints.length > 2) {
        try {
          const bounds = new maplibregl.LngLatBounds([model.polyPoints[0].lon, model.polyPoints[0].lat], [model.polyPoints[0].lon, model.polyPoints[0].lat]);
          for (const pt of model.polyPoints) bounds.extend([pt.lon, pt.lat]);
          map.fitBounds(bounds, { padding: 20, maxZoom: 19, duration: 0 });
        } catch (e) {
          console.warn("Could not fitBounds on mount", e);
        }
      }
      console.log("MissionSimulation2D style.load completed!");
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []); // Only runs once on mount

  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource('autonomous-survivors') as maplibregl.GeoJSONSource | undefined;
    source?.setData(canonicalSurvivorFeatures(targets));
  }, [targets]);

  // Update Trail when model.trail changes (this is relatively low frequency, ~5Hz, so it's fine for React/MapLibre)
  useEffect(() => {
    const map = mapRef.current;
    if (map && map.isStyleLoaded()) {
      const trailSource = map.getSource('trail') as maplibregl.GeoJSONSource;
      if (trailSource) {
        trailSource.setData({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: model.trail.map(p => [p.lon, p.lat])
          },
          properties: {}
        });
      }
    }
  }, [model.trail]);

  // Request Animation Frame loop for smooth 60fps drone interpolation!
  // NO REACT STATE UPDATES in this loop!
  useEffect(() => {
    let frame: number;
    let seqDisplay = document.getElementById("diag-seq");
    let speedDisplay = document.getElementById("diag-speed");
    
    const update = () => {
      const buffer = telemetryBufferRef.current;
      const marker = droneMarkerRef.current;
      
      if (buffer.length > 0 && marker) {
        const targetTime = performance.now() - 250;
        let before: VehicleState | null = null;
        let after: VehicleState | null = null;

        for (let i = buffer.length - 1; i >= 0; i--) {
          const sample = buffer[i];
          if (sample._receive_time_ms && sample._receive_time_ms <= targetTime) {
            before = sample;
            if (i < buffer.length - 1) after = buffer[i + 1];
            break;
          }
        }

        let lat = 0, lon = 0, yaw = 0, seq = "-", speed = 0, alt = 0;

        if (before && after && before._receive_time_ms && after._receive_time_ms) {
          const dt = after._receive_time_ms - before._receive_time_ms;
          const alpha = dt > 0 ? (targetTime - before._receive_time_ms) / dt : 0;
          lat = before.latitude + (after.latitude - before.latitude) * alpha;
          lon = before.longitude + (after.longitude - before.longitude) * alpha;
          let dyaw = after.yaw_deg - before.yaw_deg;
          if (dyaw > 180) dyaw -= 360;
          if (dyaw < -180) dyaw += 360;
          yaw = before.yaw_deg + dyaw * alpha;
          seq = before.sequence.toString();
          speed = before.ground_speed_m_s;
          alt = before.relative_altitude_m;
        } else if (before) {
          lat = before.latitude; lon = before.longitude; yaw = before.yaw_deg;
          seq = before.sequence.toString(); speed = before.ground_speed_m_s; alt = before.relative_altitude_m;
        } else if (buffer.length > 0) {
          lat = buffer[0].latitude; lon = buffer[0].longitude; yaw = buffer[0].yaw_deg;
          seq = buffer[0].sequence.toString(); speed = buffer[0].ground_speed_m_s; alt = buffer[0].relative_altitude_m;
        }
        
        if (lat !== 0 && lon !== 0) {
          marker.setLngLat([lon, lat]);
          marker.setRotation(yaw);
          if (!seqDisplay) seqDisplay = document.getElementById("diag-seq");
          if (!speedDisplay) speedDisplay = document.getElementById("diag-speed");
          if (seqDisplay) seqDisplay.innerText = seq;
          if (speedDisplay) speedDisplay.innerText = `${alt.toFixed(1)}m | ${speed.toFixed(1)}m/s`;
        }
      }
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div data-testid="mission-simulation-2d-root" className="mission-simulation" style={{ width: "100%", height: "100%", minHeight: "500px", position: "relative", backgroundColor: "#000" }}>
      <div ref={mapContainerRef} style={{ width: "100%", height: "100%", position: "absolute", top: 0, left: 0 }} />
      <div style={{ position: "absolute", top: 10, left: 10, background: "rgba(0,0,0,0.7)", color: "white", padding: "10px", borderRadius: "8px", fontFamily: "monospace", fontSize: "12px", pointerEvents: "none", zIndex: 10 }}>
        <div>STATE: {model.telemetryState === "ENDED" ? "ENDED" : model.missionPhase}</div>
        <div>TRAIL: {model.trail.length}</div>
        <div>SEQ: <span id="diag-seq">-</span></div>
        <div>TEL: <span id="diag-speed">-</span></div>
        <div>{model.telemetryState !== "LIVE" ? `LINK: ${model.telemetryState}` : ""}</div>
      </div>
    </div>
  );
}
