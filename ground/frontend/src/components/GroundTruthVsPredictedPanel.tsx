import React, { useState } from "react";
import type { Target } from "../types/domain";

export interface ScenarioSurvivor {
  id: string;
  lat?: number;
  latitude?: number;
  lon?: number;
  lng?: number;
  longitude?: number;
  bodyState?: string;
}

interface Props {
  scenarioSurvivors?: ScenarioSurvivor[];
  targets: Record<string, Target>;
  onSelectSurvivor?: (id: string) => void;
  selectedId?: string | null;
  onClose?: () => void;
}

function haversineDistanceMeters(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number
): number {
  const R = 6378137; // Earth's mean radius in meters
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

interface SurvivorRow {
  id: string;
  truthCoords: string;
  predCoords: string;
  error: string;
  status: string;
  confidence: string;
  truthLat?: number;
  truthLon?: number;
  predLat?: number;
  predLon?: number;
}

export const GroundTruthVsPredictedPanel = React.memo(function GroundTruthVsPredictedPanel({
  scenarioSurvivors = [],
  targets = {},
  onSelectSurvivor,
  selectedId,
  onClose,
}: Props) {
  const [isMinimized, setIsMinimized] = useState(false);
  const targetList: Target[] = Object.values(targets || {}).filter(
    (t) => t.state !== "REJECTED" && t.state !== "CANDIDATE"
  );
  const MAX_GROUND_TRUTH_MATCH_DISTANCE_M = 15.0;

  // 1. Direct ID matching if available
  const truthMatchMap = new Map<string, { target: Target; dist: number }>();
  const matchedTargetIds = new Set<string>();

  for (const truth of scenarioSurvivors) {
    for (const t of targetList) {
      if (!matchedTargetIds.has(t.target_id) && truth.id === t.target_id) {
        const truthLat = truth.lat ?? truth.latitude;
        const truthLon = truth.lon ?? truth.lng ?? truth.longitude;
        const dist = (truthLat !== undefined && truthLon !== undefined && t.latitude && t.longitude)
          ? haversineDistanceMeters(truthLat, truthLon, t.latitude, t.longitude)
          : 0;
        truthMatchMap.set(truth.id, { target: t, dist });
        matchedTargetIds.add(t.target_id);
        break;
      }
    }
  }

  // 2. Global minimum-distance pairing for remaining unmatched targets and survivors
  interface CandidatePair {
    truthId: string;
    target: Target;
    dist: number;
  }
  const candidatePairs: CandidatePair[] = [];
  for (const truth of scenarioSurvivors) {
    if (truthMatchMap.has(truth.id)) continue;
    const truthLat = truth.lat ?? truth.latitude;
    const truthLon = truth.lon ?? truth.lng ?? truth.longitude;
    if (truthLat === undefined || truthLon === undefined) continue;

    for (const t of targetList) {
      if (matchedTargetIds.has(t.target_id)) continue;
      if (t.latitude && t.longitude) {
        const dist = haversineDistanceMeters(truthLat, truthLon, t.latitude, t.longitude);
        if (dist <= MAX_GROUND_TRUTH_MATCH_DISTANCE_M) {
          candidatePairs.push({ truthId: truth.id, target: t, dist });
        }
      }
    }
  }

  // Sort candidate pairs ascending by physical distance
  candidatePairs.sort((a, b) => a.dist - b.dist);
  for (const pair of candidatePairs) {
    if (!truthMatchMap.has(pair.truthId) && !matchedTargetIds.has(pair.target.target_id)) {
      truthMatchMap.set(pair.truthId, { target: pair.target, dist: pair.dist });
      matchedTargetIds.add(pair.target.target_id);
    }
  }

  const rows: SurvivorRow[] = scenarioSurvivors.map((truth) => {
    const truthLat = truth.lat ?? truth.latitude;
    const truthLon = truth.lon ?? truth.lng ?? truth.longitude;
    const match = truthMatchMap.get(truth.id);
    const matchedTarget = match ? match.target : null;
    const errorMeters = match ? match.dist : null;

    return {
      id: truth.id,
      truthCoords:
        truthLat !== undefined && truthLon !== undefined
          ? `${truthLat.toFixed(5)}, ${truthLon.toFixed(5)}`
          : "NOT AVAILABLE",
      predCoords:
        matchedTarget && matchedTarget.latitude && matchedTarget.longitude
          ? `${matchedTarget.latitude.toFixed(5)}, ${matchedTarget.longitude.toFixed(5)}`
          : "NOT DETECTED",
      error: errorMeters !== null ? `${errorMeters.toFixed(2)} m` : "NOT AVAILABLE",
      status: matchedTarget ? matchedTarget.state : "GROUND TRUTH ONLY",
      confidence: matchedTarget?.detection_confidence
        ? `${Math.round(matchedTarget.detection_confidence * 100)}%`
        : "—",
      truthLat,
      truthLon,
      predLat: matchedTarget?.latitude,
      predLon: matchedTarget?.longitude,
    };
  });

  // Also include any autonomous detections not yet matched to scenario truth
  targetList.forEach((t: Target) => {
    if (!matchedTargetIds.has(t.target_id)) {
      rows.push({
        id: t.target_id,
        truthCoords: "NOT AVAILABLE (AUTONOMOUS)",
        predCoords: `${t.latitude.toFixed(5)}, ${t.longitude.toFixed(5)}`,
        error: "NOT AVAILABLE",
        status: t.state,
        confidence: `${Math.round(t.detection_confidence * 100)}%`,
        truthLat: undefined,
        truthLon: undefined,
        predLat: t.latitude,
        predLon: t.longitude,
      });
    }
  });

  if (isMinimized) {
    return (
      <div
        className="gt-vs-pred-panel minimized"
        data-testid="gt-vs-pred-panel"
        onClick={() => setIsMinimized(false)}
        title="Click to expand Survivor Audit Panel"
      >
        <span className="gt-min-title">🧑 Survivors ({rows.length})</span>
        <button
          className="panel-min-btn"
          onClick={(e) => {
            e.stopPropagation();
            setIsMinimized(false);
          }}
          title="Expand panel"
        >
          ＋ EXPAND
        </button>
      </div>
    );
  }

  return (
    <div className="gt-vs-pred-panel" data-testid="gt-vs-pred-panel">
      <div className="gt-panel-header">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <h4>Ground Truth vs Predicted Location</h4>
          <span className="gt-badge">REAL AUDIT</span>
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
              title="Close panel"
              aria-label="Close"
            >
              ✕
            </button>
          )}
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="gt-empty">No scenario survivors or autonomous targets active.</div>
      ) : (
        <div className="gt-table-wrap">
          <table className="gt-table">
            <thead>
              <tr>
                <th>Survivor ID</th>
                <th>Ground Truth</th>
                <th>Predicted</th>
                <th>Geo Error</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className={selectedId === row.id ? "selected-row" : ""}
                  onClick={() => onSelectSurvivor?.(row.id)}
                >
                  <td className="survivor-id-cell">
                    <span className="status-dot" data-status={row.status} />
                    {row.id}
                  </td>
                  <td className="coord-cell">{row.truthCoords}</td>
                  <td className="coord-cell">{row.predCoords}</td>
                  <td className={`error-cell ${row.error !== "NOT AVAILABLE" && row.error !== "Not available" ? "has-error" : ""}`}>
                    {row.error}
                  </td>
                  <td>
                    <span className={`status-pill status-${row.status.toLowerCase()}`}>
                      {row.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
});
