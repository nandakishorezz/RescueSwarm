import type { StyleSpecification } from "maplibre-gl";

/**
 * Mission map style — uses OSM raster tiles for geographic context.
 * Deliberately has NO glyphs URL so the map.on("load") event fires immediately
 * without waiting for any external font server (which may be unreachable).
 * All text/symbol layers in MapView use circle markers instead.
 */
export const blankStyle: StyleSpecification = {
  version: 8,
  sources: {
    "osm": {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "offline-background",
      type: "background",
      paint: { "background-color": "#0b1918" },
    },
    {
      id: "osm-layer",
      type: "raster",
      source: "osm",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};

/**
 * Setup wizard map style — same tiles + glyphs for emoji symbol layers.
 * Only used in SimulationWorkspaceMap where internet is expected during setup.
 */
export const setupStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    "osm": {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "offline-background",
      type: "background",
      paint: { "background-color": "#0b1918" },
    },
    {
      id: "osm-layer",
      type: "raster",
      source: "osm",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};
