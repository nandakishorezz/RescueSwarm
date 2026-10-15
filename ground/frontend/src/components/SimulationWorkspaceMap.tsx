import { useEffect, useRef } from "react";
import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { setupStyle } from "./mapStyle";

export interface SimulationWorkspaceMapProps {
  mode: "DRAW_POLYGON" | "PLACE_SURVIVOR" | "PLACE_START" | "VIEW";
  polygon: [number, number][];
  onChangePolygon: (polygon: [number, number][]) => void;
  survivors: { id: string; lng: number; lat: number }[];
  onPlaceSurvivor: (lng: number, lat: number) => void;
  startPosition: { lng: number; lat: number } | null;
  onPlaceStart: (lng: number, lat: number) => void;
  center?: [number, number];
}

/** Compute the LngLatBounds that wraps all polygon points */
function polygonBounds(polygon: [number, number][]): maplibregl.LngLatBounds | null {
  if (polygon.length === 0) return null;
  const bounds = new maplibregl.LngLatBounds(polygon[0], polygon[0]);
  for (const pt of polygon) bounds.extend(pt);
  return bounds;
}

export function SimulationWorkspaceMap({
  mode,
  polygon,
  onChangePolygon,
  survivors,
  onPlaceSurvivor,
  startPosition,
  onPlaceStart,
  center,
}: SimulationWorkspaceMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);

  // Use refs so event listeners always have the latest values without being re-registered
  const modeRef = useRef(mode);
  const polygonRef = useRef(polygon);
  modeRef.current = mode;
  polygonRef.current = polygon;

  // Mount map once
  useEffect(() => {
    if (!container.current || mapRef.current) return;

    // If the polygon already exists, start the map centred on it
    const existingBounds = polygonBounds(polygon);
    const initCenter: [number, number] = existingBounds
      ? [
          (existingBounds.getEast() + existingBounds.getWest()) / 2,
          (existingBounds.getNorth() + existingBounds.getSouth()) / 2,
        ]
      : (center ?? [77.5946, 12.9716]);

    const map = new maplibregl.Map({
      container: container.current,
      style: setupStyle,
      center: initCenter,
      zoom: 16,
      attributionControl: false,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-left");

    map.on("style.load", () => {
      // ── Sources ────────────────────────────────────────────────────────────
      map.addSource("polygon", {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "Polygon", coordinates: [[]] }, properties: {} },
      });
      map.addLayer({ id: "polygon-fill", type: "fill", source: "polygon", paint: { "fill-color": "#34d399", "fill-opacity": 0.2 } });
      map.addLayer({ id: "polygon-line", type: "line", source: "polygon", paint: { "line-color": "#54e7b1", "line-width": 2 } });

      map.addSource("survivors", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "survivor-markers",
        type: "symbol",
        source: "survivors",
        layout: { "text-field": "🧍", "text-size": 18, "text-offset": [0, -0.2] },
      });

      map.addSource("start-pos", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "start-marker",
        type: "symbol",
        source: "start-pos",
        layout: { "text-field": "🚁", "text-size": 22 },
      });

      // ── KEY FIX: immediately fit to the drawn polygon ───────────────────
      const b = polygonBounds(polygonRef.current);
      if (b) {
        map.fitBounds(b, { padding: 80, maxZoom: 19, duration: 600 });
      }

      // Pre-populate all data that may already exist
      if (polygonRef.current.length > 0) {
        const coords = polygonRef.current;
        (map.getSource("polygon") as maplibregl.GeoJSONSource).setData({
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [coords.length > 2 ? [...coords, coords[0]] : coords],
          },
          properties: {},
        });
      }

      if (survivors.length > 0) {
        (map.getSource("survivors") as maplibregl.GeoJSONSource).setData({
          type: "FeatureCollection",
          features: survivors.map((s) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [s.lng, s.lat] },
            properties: { id: s.id },
          })),
        });
      }

      if (startPosition) {
        (map.getSource("start-pos") as maplibregl.GeoJSONSource).setData({
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              geometry: { type: "Point", coordinates: [startPosition.lng, startPosition.lat] },
              properties: {},
            },
          ],
        });
      }
    });

    map.on("click", (e) => {
      const { lng, lat } = e.lngLat;
      const m = modeRef.current;
      console.log("MAP CLICKED: mode=", m, "lng=", lng, "lat=", lat, "current_poly=", polygonRef.current.length);
      if (m === "DRAW_POLYGON") {
        onChangePolygon([...polygonRef.current, [lng, lat]]);
      } else if (m === "PLACE_SURVIVOR") {
        onPlaceSurvivor(lng, lat);
      } else if (m === "PLACE_START") {
        onPlaceStart(lng, lat);
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Keep polygon source in sync and auto-fit while drawing ───────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("polygon") as maplibregl.GeoJSONSource;
    if (source) {
      source.setData({
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [polygon.length > 2 ? [...polygon, polygon[0]] : polygon],
        },
        properties: {},
      });
    }
    // Re-fit as new points are added so the user can always see their work
    if (mode === "DRAW_POLYGON" && polygon.length >= 2) {
      const b = polygonBounds(polygon);
      if (b) map.fitBounds(b, { padding: 80, maxZoom: 19, duration: 300 });
    }
  }, [polygon]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Keep survivors source in sync ────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("survivors") as maplibregl.GeoJSONSource;
    if (source) {
      source.setData({
        type: "FeatureCollection",
        features: survivors.map((s) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [s.lng, s.lat] },
          properties: { id: s.id },
        })),
      });
    }
  }, [survivors]);

  // ── Keep start-pos source in sync ────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("start-pos") as maplibregl.GeoJSONSource;
    if (source) {
      source.setData({
        type: "FeatureCollection",
        features: startPosition
          ? [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [startPosition.lng, startPosition.lat] },
                properties: {},
              },
            ]
          : [],
      });
    }
  }, [startPosition]);

  return <div ref={container} style={{ width: "100%", height: "400px", background: "#000" }} />;
}
