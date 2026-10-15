import { useEffect, useState } from "react";

import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
import { MapView } from "../components/MapView";
import { ReplayPlayer } from "../components/ReplayPlayer";
import type { CoveragePlan, GeoPoint, MissionEvent, MissionExecution } from "../types/domain";

interface ReviewData {
  targets_found?: number;
  targets_confirmed?: number;
  deliveries_completed?: number;
  vehicle_start_battery?: number;
  vehicle_end_battery?: number;
  events?: MissionEvent[];
  execution_source?: string;
  coverage_plan?: CoveragePlan | null;
  execution?: MissionExecution | null;
}

export function ReviewPage() {
  const mission = useGcsStore((state) => state.mission);
  const setError = useGcsStore((state) => state.setError);
  const [review, setReview] = useState<ReviewData>({});
  const [artifacts, setArtifacts] = useState<{ name: string; url: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const fleet = useGcsStore((state) => state.fleetStatus);
  const durationSeconds = review.execution?.started_at && review.execution.completed_at
    ? (new Date(review.execution.completed_at).getTime() - new Date(review.execution.started_at).getTime()) / 1000
    : mission?.elapsed_seconds;
  const warningCount = review.events?.filter((event) => event.severity !== "INFO").length ?? 0;
  const failureEvents = review.events?.filter((event) => ["FAILURE_INJECTED", "SAFETY_DECISION", "NATIVE_FAILSAFE_DETECTED", "INVARIANT_VIOLATION"].includes(event.type)) ?? [];
  const recoveryEvents = review.events?.filter((event) => event.type.startsWith("RECOVERY_")) ?? [];
  useEffect(() => {
    if (mission) { setLoading(true); Promise.all([api.review(mission.id), api.replay(mission.id), api.artifacts(mission.id)])
      .then(([value, replay, artifactResult]) => {
        const loaded = value as ReviewData;
        setReview(loaded);
        useGcsStore.getState().setReplay(replay);
        setArtifacts(artifactResult.artifacts);
        if (loaded.coverage_plan) useGcsStore.getState().setCoveragePlan(loaded.coverage_plan);
        if (loaded.execution) useGcsStore.setState((state) => ({
          executionSource: loaded.execution!.source,
          rescueState: (loaded.execution!.metadata?.rescue_state as typeof state.rescueState | undefined) ?? state.rescueState,
          payloadRemaining: typeof loaded.execution!.metadata?.payload_inventory_remaining === "number"
            ? loaded.execution!.metadata.payload_inventory_remaining : state.payloadRemaining,
          trails: {
            ...state.trails,
            drone_01: loaded.execution!.actual_trail.map((point) => [point.longitude, point.latitude]),
          },
        }));
        const approach = loaded.events?.find((event) => event.type === "APPROACH_STARTED");
        const path = approach?.metadata?.path_wgs84;
        if (Array.isArray(path)) useGcsStore.setState({ guidedApproachRoute: path as GeoPoint[] });
        setError(null); setLoading(false);
      })
      .catch((error) => { setError(`REVIEW UNAVAILABLE · ${error instanceof Error ? error.message : "UNKNOWN"}`); setLoading(false); }); }
  }, [mission, setError]);
  return (
    <main className="review-page">
      <div className="review-heading"><div><p>MISSION REVIEW</p><h1>{mission?.name ?? "No mission selected"}</h1><span>{mission?.state ?? "NOT STARTED"} · {mission?.elapsed_seconds.toFixed(0) ?? 0} s</span></div><button onClick={() => window.print()}>PRINT / SAVE REPORT</button></div>
      {loading && <div className="loading-state" role="status"><i aria-hidden="true" />Loading recorded mission evidence…</div>}
      {!mission && <div className="empty-state"><b>NO REPLAY DATA</b><span>Select or complete a mission to review its recorded telemetry and events.</span></div>}
      <section className="review-metrics">
        <article><span>FLEET STATE</span><strong>{fleet?.state ?? "NOT RUN"}</strong></article>
        <article><span>FLEET SIZE</span><strong>{fleet?.fleet_size ?? 0}</strong></article>
        <article><span>COMBINED COVERAGE</span><strong>{fleet?.progress?.combined_coverage_percent?.toFixed(1) ?? "—"}%</strong></article>
        <article><span>EXECUTION SOURCE</span><strong>{review.execution_source ?? "SIMULATED"}</strong></article>
        <article><span>AREA</span><strong>{mission?.boundary?.area_hectares.toFixed(2) ?? "—"} ha</strong></article>
        <article><span>SAFE SEARCH AREA</span><strong>{review.coverage_plan ? `${Number(review.coverage_plan.metadata.safe_area_m2 ?? 0).toFixed(0)} m²` : "—"}</strong></article>
        <article><span>FLIGHT DURATION</span><strong>{durationSeconds?.toFixed(1) ?? "—"} s</strong></article>
        <article><span>FINAL STATE</span><strong>{review.execution?.state ?? mission?.state ?? "—"}</strong></article>
        <article><span>SWEEP</span><strong>{review.coverage_plan?.sweep_angle_deg.toFixed(1) ?? "—"}°</strong></article>
        <article><span>LANE / ALTITUDE</span><strong>{review.coverage_plan ? `${review.coverage_plan.configuration.lane_spacing_m.toFixed(1)} / ${review.coverage_plan.configuration.search_altitude_m.toFixed(1)} m` : "—"}</strong></article>
        <article><span>SEARCH SPEED</span><strong>{review.coverage_plan?.configuration.search_speed_m_s.toFixed(1) ?? "—"} m/s</strong></article>
        <article><span>PLANNED COVERAGE</span><strong>{review.coverage_plan?.validation.planned_coverage_percent.toFixed(1) ?? "—"}%</strong></article>
        <article><span>ACTUAL COVERAGE</span><strong>{review.execution?.actual_coverage_percent.toFixed(1) ?? mission?.coverage_percent.toFixed(0) ?? 0}%</strong></article>
        <article><span>PLANNED DISTANCE</span><strong>{review.coverage_plan?.estimated_path_length_m.toFixed(0) ?? "—"} m</strong></article>
        <article><span>ACTUAL DISTANCE</span><strong>{review.execution?.actual_path_length_m.toFixed(0) ?? "—"} m</strong></article>
        <article><span>WAYPOINTS</span><strong>{review.execution?.waypoints_reached.length ?? 0} / {review.execution?.total_mission_items ?? "—"}</strong></article>
        <article><span>PATH DEVIATION P95 / MAX</span><strong>{review.execution?.p95_deviation_m?.toFixed(1) ?? "—"} / {review.execution?.max_deviation_m?.toFixed(1) ?? "—"} m</strong></article>
        <article><span>RTL / LAND / DISARM</span><strong>{review.execution ? `${review.execution.rtl_completed ? "✓" : "—"} / ${review.execution.landing_detected ? "✓" : "—"} / ${review.execution.disarm_detected ? "✓" : "—"}` : "—"}</strong></article>
        <article><span>WARNINGS / ERRORS</span><strong>{warningCount} / {review.execution?.errors.length ?? 0}</strong></article>
        <article><span>FAILURES / RECOVERIES</span><strong>{failureEvents.length} / {recoveryEvents.length}</strong></article>
        <article><span>FINAL SAFETY OWNER</span><strong>{String(failureEvents.at(-1)?.metadata?.authoritative_layer ?? "—")}</strong></article>
        <article><span>TARGETS FOUND</span><strong>{review.targets_found ?? 0}</strong></article>
        <article><span>CONFIRMED</span><strong>{review.targets_confirmed ?? 0}</strong></article>
        <article><span>DELIVERIES</span><strong>{review.deliveries_completed ?? 0}</strong></article>
        <article><span>RESCUE STATE</span><strong>{String(review.execution?.metadata?.rescue_state ?? "NOT RUN")}</strong></article>
        <article><span>RESUME POLICY</span><strong>{String(review.execution?.metadata?.resume_policy ?? "—")}</strong></article>
        <article><span>RESUME SEQ</span><strong>{String(review.execution?.metadata?.resume_requested_seq ?? "—")} → {String(review.execution?.metadata?.resume_actual_seq ?? "—")}</strong></article>
        <article><span>PAYLOAD SOURCE</span><strong>{String((review.execution?.metadata?.payload as Record<string, unknown> | undefined)?.execution_source ?? "—")}</strong></article>
        <article><span>BATTERY</span><strong>{review.vehicle_start_battery ?? "—"}% → {review.vehicle_end_battery?.toFixed(0) ?? "—"}%</strong></article>
      </section>
      {review.coverage_plan && <section className="review-map"><h2>PLANNED ROUTE VS ACTUAL TRAIL</h2><MapView /></section>}
      <section className="review-timeline"><h2>MISSION EVENT TIMELINE</h2>
        {!review.events?.length && <p className="empty">Complete the GCS simulation to populate review evidence.</p>}
        {review.events?.map((event) => <article key={event.event_id}><time>{new Date(event.timestamp).toLocaleTimeString()}</time><b>{event.type.replaceAll("_", " ")}</b><span>{event.message}</span></article>)}
      </section>
      <section className="review-replay"><div className="panel-title"><h2>DETERMINISTIC 3D FLIGHT REPLAY</h2><span className="sim-tag">RECORDED TIMESTAMPS</span></div><ReplayPlayer /></section>
      <section className="artifact-links"><h2>LOCAL EVIDENCE REPORTS</h2>{!artifacts.length && <p className="empty">NO EXPOSED REPORT ARTIFACTS</p>}{artifacts.map((artifact) => <a href={artifact.url} target="_blank" rel="noreferrer" key={artifact.name}>{artifact.name}</a>)}</section>
    </main>
  );
}
