import { useGcsStore } from "../stores/gcsStore";
import type { ScaleMode, ViewMode } from "../types/domain";

export function ViewControls() {
  const view = useGcsStore((state) => state.viewMode); const setView = useGcsStore((state) => state.setViewMode);
  const scale = useGcsStore((state) => state.scaleMode); const setScale = useGcsStore((state) => state.setScaleMode);
  const ratio = useGcsStore((state) => state.splitRatio); const setRatio = useGcsStore((state) => state.setSplitRatio);
  const layers = useGcsStore((state) => state.digitalTwinLayers); const setLayer = useGcsStore((state) => state.setDigitalTwinLayer);
  const layerLabels: Record<string, string> = {
    aircraft: "Aircraft", paths: "Paths", sectors: "Sectors", cameraFrustum: "Camera frustum",
    cameraFootprint: "Camera footprint", detectionRays: "Detection rays", targets: "Targets",
    uncertainty: "Uncertainty", axes: "Axes",
  };
  return <div className="view-controls" aria-label="Digital twin view controls">
    {(["2D", "3D", "SPLIT"] as ViewMode[]).map((item) => <button key={item} className={view === item ? "active" : ""} onClick={() => setView(item)}>{item}</button>)}
    {(["ICON", "TRUE_SCALE"] as ScaleMode[]).map((item) => <button key={item} className={scale === item ? "active" : ""} onClick={() => setScale(item)}>{item.replace("_", " ")}</button>)}
    {view === "SPLIT" && <div className="split-ratios" aria-label="Split view ratio">{(["35_65", "50_50", "65_35"] as const).map((item) => <button key={item} className={ratio === item ? "active" : ""} onClick={() => setRatio(item)} title={`Map/3D ${item.replace("_", "/")}`}>{item.replace("_", "/")}</button>)}</div>}
    <details className="layer-menu"><summary>LAYERS</summary><div>{Object.entries(layerLabels).map(([id, label]) => <label key={id}><input type="checkbox" checked={layers[id] !== false} onChange={(event) => setLayer(id, event.target.checked)} />{label}</label>)}</div></details>
  </div>;
}
