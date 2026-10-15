import { useEffect, useState } from "react";

import { useGcsStore } from "../stores/gcsStore";
import { detectionLabel, detectionStyle, validDetections } from "./cameraDetections";

export function CameraFeedPanel() {
  const cameraFrame = useGcsStore((state) => state.cameraFrame);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!cameraFrame || !cameraFrame.image_b64) {
    return <div style={{ padding: "12px", color: "#94a3b8", fontSize: "0.8rem", textAlign: "center" }}>NO FRAMES — waiting for Gazebo camera</div>;
  }

  const ageSeconds = Math.max(0, now - Number(cameraFrame.source_ts ?? now));
  const live = ageSeconds < 3;
  const modelStatus = cameraFrame.model_status ?? "UNKNOWN";
  const detections = validDetections(cameraFrame.detections);
  const visibleDetections = live ? detections : [];
  const sourceWidth = Number(cameraFrame.detection_image_width ?? cameraFrame.image_width ?? 640);
  const sourceHeight = Number(cameraFrame.detection_image_height ?? cameraFrame.image_height ?? 400);

  return (
    <div style={{ padding: "8px", display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ position: "relative", width: "100%", borderRadius: "4px", overflow: "hidden", background: "#000" }}>
        <img 
          src={`data:image/jpeg;base64,${cameraFrame.image_b64}`} 
          alt="Live Camera Feed"
          style={{ width: "100%", display: "block" }} 
        />
        {visibleDetections.map((detection, index) => (
          <div
            key={`${cameraFrame.frame_id ?? cameraFrame.frame_count ?? "frame"}-${index}`}
            data-testid="camera-detection"
            style={{
              position: "absolute", boxSizing: "border-box", border: "2px solid #4ade80",
              pointerEvents: "none", ...detectionStyle(detection, sourceWidth, sourceHeight),
            }}
          >
            <span style={{ position: "absolute", left: "-2px", top: "-20px", whiteSpace: "nowrap", background: "#166534", color: "#fff", padding: "2px 4px", fontSize: "0.65rem" }}>
              {detectionLabel(detection)} {(detection.conf * 100).toFixed(1)}%
            </span>
          </div>
        ))}
        <div style={{ position: "absolute", top: "4px", right: "4px", background: "rgba(0,0,0,0.6)", padding: "2px 6px", borderRadius: "2px", fontSize: "0.7rem", color: "#fff", display: "flex", gap: "6px" }}>
            <span style={{ color: live ? "#4ade80" : "#fbbf24" }}>{live ? "LIVE" : "STALE"} {ageSeconds.toFixed(1)}s</span>
            <span style={{ color: visibleDetections.length > 0 ? "#4ade80" : "#fff" }}>
              {visibleDetections.length} DETECTIONS
            </span>
            <span style={{ color: modelStatus === "READY" ? "#94a3b8" : "#f87171" }}>
              {modelStatus === "READY" && typeof cameraFrame.inference_time === "number"
                ? `${(cameraFrame.inference_time * 1000).toFixed(0)}ms infer` : `MODEL ${modelStatus}`}
            </span>
        </div>
      </div>
      <div style={{ fontSize: "0.7rem", color: "#94a3b8" }}>
        Frames {cameraFrame.frame_count ?? "—"} · decoded {cameraFrame.decoded_frame_count ?? "—"} · inferred {cameraFrame.inference_count ?? "—"}
        {cameraFrame.model_error ? ` · ${cameraFrame.model_error}` : ""}
      </div>
      
      {live && visibleDetections.length > 0 && (
        <div style={{ fontSize: "0.75rem", color: "#cbd5e1" }}>
          <strong>Detections:</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: "20px" }}>
            {visibleDetections.map((detection, i) => (
              <li key={i}>{detectionLabel(detection)} ({(detection.conf * 100).toFixed(1)}%)
                {detection.geolocation?.status === "APPROXIMATE" && <> · {detection.geolocation.target_id?.slice(0, 8) ?? "pending"} · approx ±{detection.geolocation.uncertainty_m?.toFixed(1) ?? "—"} m</>}
                {detection.geolocation?.status === "UNAVAILABLE" && <> · location unavailable</>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
