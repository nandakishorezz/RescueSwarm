import React, { useState } from "react";
import type { VehicleState } from "../types/domain";

interface Props {
  telemetry2D: {
    lat: number;
    lon: number;
    yaw: number;
    alt: number;
    speed: number;
    seq: string;
  };
  drone3D: {
    lat: number;
    lon: number;
    yaw: number;
    alt: number;
    sceneX: number;
    sceneY: number;
    sceneZ: number;
  };
  liveVehicle: VehicleState | null;
  telemetryState: "PREVIEW" | "LIVE" | "DEGRADED" | "STALE" | "ENDED";
  isCustomModel: boolean;
  fps: number;
  onClose?: () => void;
}

export const DiagnosticTelemetryReadout = React.memo(function DiagnosticTelemetryReadout({
  telemetry2D,
  drone3D,
  liveVehicle,
  telemetryState,
  isCustomModel,
  fps,
  onClose,
}: Props) {
  const [isMinimized, setIsMinimized] = useState(false);
  const latDelta = Math.abs(telemetry2D.lat - drone3D.lat);
  const lonDelta = Math.abs(telemetry2D.lon - drone3D.lon);
  const altDelta = Math.abs(telemetry2D.alt - drone3D.alt);
  const yawDelta = Math.abs(telemetry2D.yaw - drone3D.yaw);

  // Delta in millimeters on ground: ~111,139 meters per degree latitude
  const posErrorMm = Math.hypot(latDelta * 111139, lonDelta * 111139 * Math.cos((telemetry2D.lat * Math.PI) / 180)) * 1000;

  if (isMinimized) {
    return (
      <div
        className="telemetry-diagnostic-overlay minimized"
        data-testid="diagnostic-readout"
        onClick={() => setIsMinimized(false)}
        title="Click to expand Telemetry Sync Readout"
      >
        <span className="diag-min-title">
          📊 SYNC: {telemetryState} ({Math.round(fps)} FPS) · Δ {posErrorMm.toFixed(1)}mm
        </span>
        <button
          className="panel-min-btn"
          onClick={(e) => {
            e.stopPropagation();
            setIsMinimized(false);
          }}
          title="Expand readout"
        >
          ＋ EXPAND
        </button>
      </div>
    );
  }

  return (
    <div className="telemetry-diagnostic-overlay" data-testid="diagnostic-readout">
      <div className="diag-header">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span className="diag-title">2D vs 3D TELEMETRY SYNC</span>
          <span className={`diag-link-badge link-${telemetryState.toLowerCase()}`}>
            {telemetryState}
          </span>
        </div>
        <div className="panel-header-actions">
          <button
            className="panel-action-btn"
            onClick={() => setIsMinimized(true)}
            title="Minimize to small badge"
            aria-label="Minimize"
          >
            _ MINIMIZE
          </button>
          {onClose && (
            <button
              className="panel-action-btn panel-close-btn"
              onClick={onClose}
              title="Close readout"
              aria-label="Close"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      <div className="diag-grid">
        <div className="diag-column">
          <div className="col-label">2D MAP MARKER</div>
          <div className="diag-row">
            <span>Lat:</span>
            <code>{telemetry2D.lat !== 0 ? telemetry2D.lat.toFixed(6) : "—"}</code>
          </div>
          <div className="diag-row">
            <span>Lon:</span>
            <code>{telemetry2D.lon !== 0 ? telemetry2D.lon.toFixed(6) : "—"}</code>
          </div>
          <div className="diag-row">
            <span>Alt:</span>
            <code>{telemetry2D.alt.toFixed(1)} m</code>
          </div>
          <div className="diag-row">
            <span>Yaw:</span>
            <code>{telemetry2D.yaw.toFixed(1)}°</code>
          </div>
        </div>

        <div className="diag-column">
          <div className="col-label">3D SCENE DRONE</div>
          <div className="diag-row">
            <span>Lat:</span>
            <code>{drone3D.lat !== 0 ? drone3D.lat.toFixed(6) : "—"}</code>
          </div>
          <div className="diag-row">
            <span>Lon:</span>
            <code>{drone3D.lon !== 0 ? drone3D.lon.toFixed(6) : "—"}</code>
          </div>
          <div className="diag-row">
            <span>Alt (Y):</span>
            <code>{drone3D.alt.toFixed(1)} m</code>
          </div>
          <div className="diag-row">
            <span>Yaw:</span>
            <code>{drone3D.yaw.toFixed(1)}°</code>
          </div>
        </div>
      </div>

      <div className="diag-tolerance-bar">
        <span>SYNC TOLERANCE:</span>
        <strong className={posErrorMm < 10 ? "text-good" : "text-warn"}>
          ΔPos {posErrorMm < 1000 ? `${posErrorMm.toFixed(1)} mm` : `${(posErrorMm / 1000).toFixed(3)} m`}
        </strong>
        <span>· ΔAlt {altDelta.toFixed(2)}m</span>
        <span>· ΔYaw {yawDelta.toFixed(1)}°</span>
      </div>

      <div className="diag-footer">
        <span>Scene Pos: [{drone3D.sceneX.toFixed(1)}, {drone3D.sceneY.toFixed(1)}, {drone3D.sceneZ.toFixed(1)}]</span>
        <span>Mode: {liveVehicle?.flight_mode ?? "IDLE"}</span>
        <span>Armed: {liveVehicle?.armed ? "YES" : "NO"}</span>
        <span>Model: {isCustomModel ? "ACTUAL 3D GLTF" : "PROCEDURAL"}</span>
        <span>FPS: {Math.round(fps)}</span>
      </div>
    </div>
  );
});
