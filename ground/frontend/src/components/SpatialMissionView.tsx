import { lazy, Suspense, useCallback, useState } from "react";

import { useGcsStore } from "../stores/gcsStore";
import type { RenderMetrics } from "./DigitalTwinView";
import { ViewControls } from "./ViewControls";

const DigitalTwinView = lazy(() => import("./DigitalTwinView").then((module) => ({ default: module.DigitalTwinView })));
const MapView = lazy(() => import("./MapView").then((module) => ({ default: module.MapView })));

export function SpatialMissionView() {
  const view = useGcsStore((state) => state.viewMode);
  const mode = useGcsStore((state) => state.mode);
  const splitRatio = useGcsStore((state) => state.splitRatio);
  const [metrics, setMetrics] = useState<RenderMetrics | null>(null);
  const onMetrics = useCallback((value: RenderMetrics) => setMetrics(value), []);
  return <section className={`spatial-view spatial-${view.toLowerCase()} split-${splitRatio.toLowerCase()}`}>
    <div className="spatial-toolbar"><ViewControls /><details className="spatial-legend"><summary>LEGEND</summary><div><span>━ Planned search</span><span>━ Actual search</span><span>┄ Guided diversion</span><span>▧ Sector</span><span>◉ Target ± uncertainty</span><span>□ Launch zone</span><span>△ Camera</span></div></details>
      {mode === "ENGINEERING" && metrics && <small>{metrics.fps.toFixed(0)} FPS · P50 {metrics.frameTimeP50Ms.toFixed(1)} ms · P95 {metrics.frameTimeP95Ms.toFixed(1)} ms · {metrics.objects} objects{metrics.memoryMb ? ` · ${metrics.memoryMb.toFixed(0)} MB` : ""}</small>}
    </div>
    <Suspense fallback={<div className="spatial-loading">LOADING LOCAL SPATIAL VIEW…</div>}>
      {view === "2D" && <MapView />}
      {view === "3D" && <DigitalTwinView onMetrics={onMetrics} />}
      {view === "SPLIT" && <><MapView /><DigitalTwinView onMetrics={onMetrics} /></>}
    </Suspense>
  </section>;
}
