import { useEffect, useMemo, useRef } from "react";

import type { Target } from "../types/domain";

export interface SimulationTruthSurvivor {
  survivor_id: string;
  enabled: boolean;
  position_wgs84: [number, number, number];
}

export interface SurvivorAccuracyMatch {
  survivorId: string;
  target: Target | null;
  errorM: number | null;
  status: "WAITING FOR DETECTION" | "ESTIMATE UNAVAILABLE" | "UNMATCHED" | "MEASURED";
}

const EARTH_RADIUS_M = 6_371_008.8;

export function horizontalDistanceM(
  first: { latitude: number; longitude: number },
  second: { latitude: number; longitude: number },
): number {
  const radians = Math.PI / 180;
  const latitude1 = first.latitude * radians;
  const latitude2 = second.latitude * radians;
  const deltaLatitude = (second.latitude - first.latitude) * radians;
  const deltaLongitude = (second.longitude - first.longitude) * radians;
  const a = Math.sin(deltaLatitude / 2) ** 2
    + Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(deltaLongitude / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

function hasEstimate(target: Target): boolean {
  return Number.isFinite(target.latitude) && Number.isFinite(target.longitude);
}

function evaluationGateM(target: Target): number {
  // This gate is evaluator-only. It never feeds identity or geolocation back
  // into autonomy. Three reported sigmas permit an honest comparison while
  // the 3-25 m bounds prevent either rounding noise or indiscriminate matches.
  const uncertainty = Number.isFinite(target.geolocation_uncertainty_m)
    ? target.geolocation_uncertainty_m
    : 0;
  return Math.min(25, Math.max(3, uncertainty * 3));
}

export function matchSurvivorLocations(
  truth: SimulationTruthSurvivor[],
  targets: Target[],
  previous: ReadonlyMap<string, string> = new Map(),
): SurvivorAccuracyMatch[] {
  const enabledTruth = truth.filter((item) => item.enabled);
  const usableTargets = targets.filter((target) => target.state !== "REJECTED" && target.state !== "CANDIDATE");
  const assignments = new Map<string, Target>();
  const usedTargets = new Set<string>();

  const distance = (item: SimulationTruthSurvivor, target: Target) => horizontalDistanceM(
    { latitude: item.position_wgs84[0], longitude: item.position_wgs84[1] },
    { latitude: target.latitude, longitude: target.longitude },
  );

  for (const item of enabledTruth) {
    const targetId = previous.get(item.survivor_id);
    const target = usableTargets.find((candidate) => candidate.target_id === targetId);
    if (target && hasEstimate(target) && distance(item, target) <= evaluationGateM(target)) {
      assignments.set(item.survivor_id, target);
      usedTargets.add(target.target_id);
    }
  }

  const candidates = enabledTruth.flatMap((item) => usableTargets
    .filter((target) => !usedTargets.has(target.target_id) && hasEstimate(target))
    .map((target) => ({ item, target, distanceM: distance(item, target) }))
    .filter(({ target, distanceM }) => distanceM <= evaluationGateM(target)));
  candidates.sort((a, b) => a.distanceM - b.distanceM
    || a.item.survivor_id.localeCompare(b.item.survivor_id)
    || a.target.target_id.localeCompare(b.target.target_id));
  for (const candidate of candidates) {
    if (assignments.has(candidate.item.survivor_id) || usedTargets.has(candidate.target.target_id)) continue;
    assignments.set(candidate.item.survivor_id, candidate.target);
    usedTargets.add(candidate.target.target_id);
  }

  return enabledTruth.map((item) => {
    const target = assignments.get(item.survivor_id) ?? null;
    if (!target) {
      return {
        survivorId: item.survivor_id,
        target: null,
        errorM: null,
        status: usableTargets.length ? "UNMATCHED" : "WAITING FOR DETECTION",
      };
    }
    if (!hasEstimate(target)) {
      return { survivorId: item.survivor_id, target, errorM: null, status: "ESTIMATE UNAVAILABLE" };
    }
    return { survivorId: item.survivor_id, target, errorM: distance(item, target), status: "MEASURED" };
  });
}

interface Props {
  runId: string | null;
  truth: SimulationTruthSurvivor[];
  targets: Target[];
  simulationOnly: boolean;
}

export function SurvivorLocationAccuracy({ runId, truth, targets, simulationOnly }: Props) {
  const previousMatches = useRef(new Map<string, string>());
  const previousRun = useRef<string | null>(runId);

  if (previousRun.current !== runId) {
    previousMatches.current.clear();
    previousRun.current = runId;
  }

  const matches = useMemo(
    () => matchSurvivorLocations(truth, targets, previousMatches.current),
    [truth, targets, runId],
  );

  useEffect(() => {
    previousMatches.current = new Map(
      matches.filter((item) => item.target).map((item) => [item.survivorId, item.target!.target_id]),
    );
  }, [matches]);

  if (!simulationOnly) return null;
  const measured = matches.filter((item) => item.errorM !== null);
  const mean = measured.length
    ? measured.reduce((sum, item) => sum + item.errorM!, 0) / measured.length
    : null;
  const maximum = measured.length ? Math.max(...measured.map((item) => item.errorM!)) : null;

  return (
    <section className="survivor-accuracy" aria-label="Survivor Location Accuracy — Simulation Evaluation">
      <h3>Survivor Location Accuracy — Simulation Evaluation</h3>
      <p className="truth-warning">Observer-only simulation reference; never used by autonomy.</p>
      <div className="survivor-accuracy-grid">
        {matches.map((match) => {
          const reference = truth.find((item) => item.survivor_id === match.survivorId)!;
          return (
            <article key={match.survivorId} data-testid="survivor-accuracy-entry">
              <header><strong>{match.survivorId}</strong><span>{match.status}</span></header>
              <dl>
                <dt>Ground truth</dt>
                <dd>{reference.position_wgs84[0].toFixed(7)}, {reference.position_wgs84[1].toFixed(7)}</dd>
                <dt>System estimate</dt>
                <dd>{match.target && hasEstimate(match.target)
                  ? `${match.target.latitude.toFixed(7)}, ${match.target.longitude.toFixed(7)}`
                  : "Unavailable"}</dd>
                <dt>Location error</dt>
                <dd className="accuracy-error">{match.errorM === null ? "—" : `${match.errorM.toFixed(2)} m`}</dd>
                <dt>Target ID</dt>
                <dd>{match.target?.target_id ?? "—"}</dd>
              </dl>
            </article>
          );
        })}
      </div>
      {!matches.length && <p>Waiting for simulation survivor reference data.</p>}
      <footer>
        Valid matches <b>{measured.length}</b> · unavailable <b>{matches.length - measured.length}</b>
        {mean !== null && <> · mean <b>{mean.toFixed(2)} m</b> · maximum <b>{maximum!.toFixed(2)} m</b></>}
      </footer>
    </section>
  );
}
