import { lazy, Suspense, useEffect } from "react";
import { ModeNav } from "../components/ModeNav";
import { StatusBar } from "../components/StatusBar";
import { useMissionSocket } from "../hooks/useMissionSocket";
import { useDigitalTwin } from "../hooks/useDigitalTwin";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";
import type { CoverageRequest, HardwareGateStatus, ResilienceStatus } from "../types/domain";

const EngineeringPage = lazy(() => import("../pages/EngineeringPage").then((module) => ({ default: module.EngineeringPage })));
const MissionPage = lazy(() => import("../pages/MissionPage").then((module) => ({ default: module.MissionPage })));
const ReviewPage = lazy(() => import("../pages/ReviewPage").then((module) => ({ default: module.ReviewPage })));
const SetupPage = lazy(() => import("../pages/SetupPage").then((module) => ({ default: module.SetupPage })));
const SimulationPage = lazy(() => import("../pages/SimulationPage").then((module) => ({ default: module.SimulationPage })));
const MultiDronePage = lazy(() => import("../features/multiDrone/MultiDronePage").then((module) => ({ default: module.MultiDronePage })));

export function App() {
  const mode = useGcsStore((state) => state.mode);
  const mission = useGcsStore((state) => state.mission);
  const error = useGcsStore((state) => state.error);
  const lastGoodUpdate = useGcsStore((state) => state.lastEnvelope?.timestamp);
  const store = useGcsStore();
  useMissionSocket(mission?.id);
  useDigitalTwin(mission?.id);

  useEffect(() => {
    const requestedMode = new URLSearchParams(window.location.search).get("mode")?.toUpperCase();
    if (["SETUP", "MISSION", "SIMULATION", "MULTIDRONE", "ENGINEERING", "REVIEW"].includes(requestedMode ?? "")) {
      store.setMode(requestedMode as "SETUP" | "MISSION" | "SIMULATION" | "MULTIDRONE" | "ENGINEERING" | "REVIEW");
    }
    const requestedView = new URLSearchParams(window.location.search).get("view")?.toUpperCase();
    if (["2D", "3D", "SPLIT"].includes(requestedView ?? "")) {
      store.setViewMode(requestedView as "2D" | "3D" | "SPLIT");
    }
    Promise.all([api.health(), api.system(), api.offline(), api.missions(), api.resilienceStatus()])
      .then(async ([, system, offline, missions, resilience]) => {
        store.setBackendState("CONNECTED");
        store.setOfflineStatus(offline);
        store.setCompetitionMode(Boolean(system.competition_mode));
        if (system.coverage_defaults) store.setCoverageDefaults(system.coverage_defaults as CoverageRequest);
        if (system.hardware_gate_h1) store.setHardwareGateH1(system.hardware_gate_h1 as HardwareGateStatus);
        store.setResilienceStatus(resilience as unknown as ResilienceStatus);
        const thresholds = system.stale_thresholds as { connected_seconds?: number; degraded_seconds?: number } | undefined;
        if (thresholds?.connected_seconds && thresholds?.degraded_seconds) {
          store.setStaleThresholds(thresholds.connected_seconds, thresholds.degraded_seconds);
        }
        const limits = system.browser_limits as { trail_max_points?: number; recent_events_max?: number } | undefined;
        if (typeof system.max_area_hectares === "number" && limits?.trail_max_points && limits?.recent_events_max) {
          store.setRuntimeLimits(system.max_area_hectares, limits.trail_max_points, limits.recent_events_max);
        }
        const requestedMissionId = new URLSearchParams(window.location.search).get("mission");
        const activeMission = missions.find((item) => item.id === requestedMissionId) ?? missions[0];
        if (activeMission) {
          store.setMission(activeMission);
          const [vehicles, targets, events, plan, fleet] = await Promise.all([
            api.vehicles(activeMission.id), api.targets(activeMission.id), api.events(activeMission.id),
            api.plan(activeMission.id).catch(() => null),
            api.fleet(activeMission.id).catch(() => null),
          ]);
          store.setInitialData(vehicles, targets, events);
          store.setCoveragePlan(plan);
          if (fleet) store.setFleetStatus(fleet);
        }
      })
      .catch((reason) => {
        store.setBackendState("DISCONNECTED");
        store.setError(`BACKEND DISCONNECTED · ${reason instanceof Error ? reason.message : "UNKNOWN"}`);
      });
    // Store actions are stable Zustand functions; initialization intentionally runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app-shell">
      <StatusBar />
      <ModeNav />
      {error && <div className="global-alert" role="alert"><div><b>MISSION DATA UNAVAILABLE</b><span>{error}</span><small>Last successful update: {lastGoodUpdate ? new Date(lastGoodUpdate).toLocaleTimeString() : "none this session"}</small></div><button onClick={() => window.location.reload()}>RETRY</button></div>}
      <Suspense fallback={<div className="page-loading">LOADING LOCAL APPLICATION ASSETS…</div>}>
        {mode === "SETUP" && <SetupPage />}
        {mode === "MISSION" && <MissionPage />}
        {mode === "SIMULATION" && <SimulationPage />}
        {mode === "MULTIDRONE" && <MultiDronePage />}
        {mode === "ENGINEERING" && <EngineeringPage />}
        {mode === "REVIEW" && <ReviewPage />}
      </Suspense>
      <footer>RESCUESWARM STAGE 13SIM PHASE 1 · HIGH-FIDELITY SOFTWARE/PHYSICS LAB · H1 PHYSICAL HARDWARE PENDING</footer>
    </div>
  );
}
