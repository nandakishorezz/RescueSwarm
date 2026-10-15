import { useMemo } from "react";
import { useGcsStore } from "../stores/gcsStore";
import type { Mission, Target, VehicleState } from "../types/domain";

export interface VisualizationModel {
  mission: Mission | null;
  polyPoints: { lat: number; lon: number }[];
  startPos: { lat: number; lon: number } | null;
  survivors: { lat: number; lon: number; id: string; state: string }[];
  liveVehicle: VehicleState | null;
  livePos: { lat: number; lon: number } | null;
  trail: { lat: number; lon: number }[];
  
  telemetryState: "LIVE" | "DEGRADED" | "STALE" | "ENDED";
  ageSeconds: number;
  missionPhase: string;
}

export function useVisualizationModel(scenario: any | null, now: number): VisualizationModel {
  const mission = useGcsStore((state) => state.mission);
  const vehicles = useGcsStore((state) => state.vehicles);
  const targets = useGcsStore((state) => state.targets);
  const trails = useGcsStore((state) => state.trails);

  return useMemo(() => {
    let polyPoints: { lat: number; lon: number }[] = [];
    if (scenario?.polygon && scenario.polygon.length > 0) {
      polyPoints = scenario.polygon.map((p: number[]) => ({ lon: p[0], lat: p[1] }));
    } else if (mission?.boundary?.vertices) {
      polyPoints = mission.boundary.vertices.map((v) => ({ lon: v.longitude, lat: v.latitude }));
    }

    let startPos: { lat: number; lon: number } | null = null;
    if (scenario?.startPosition) {
      startPos = { lat: scenario.startPosition.lat, lon: scenario.startPosition.lng };
    } else {
      const home = mission?.boundary?.origin;
      if (home && typeof home.latitude === "number" && typeof home.longitude === "number") {
        startPos = { lat: home.latitude, lon: home.longitude };
      }
    }

    let survivors = Object.values(targets)
      .filter((t: Target) => t.state !== "REJECTED" && t.state !== "CANDIDATE")
      .map((t: Target) => ({
        lat: t.latitude,
        lon: t.longitude,
        id: t.target_id,
        state: t.state,
      }));
    
    if (survivors.length === 0 && scenario?.survivors) {
      survivors = scenario.survivors.map((s: any) => ({
        lat: s.lat ?? s.latitude,
        lon: s.lng ?? s.longitude,
        id: s.id,
        state: "SCENARIO_TRUTH",
      }));
    }

    const liveVehicle = Object.values(vehicles)[0] ?? null;
    let livePos = null;
    let ageSeconds = 0;
    let telemetryState: "LIVE" | "DEGRADED" | "STALE" | "ENDED" = "LIVE";
    let missionPhase = "PENDING";
    let trailCoords: { lat: number; lon: number }[] = [];

    if (liveVehicle) {
      livePos = { lat: liveVehicle.latitude, lon: liveVehicle.longitude };
      
      const vehicleTime = liveVehicle._receive_time_ms || 0;
      ageSeconds = vehicleTime > 0 ? (now - vehicleTime) / 1000 : 0;
      
      const rawTrail = trails[liveVehicle.vehicle_id] || [];
      trailCoords = rawTrail.map(pt => ({ lon: pt[0], lat: pt[1] }));

      if (mission?.state) {
        missionPhase = mission.state;
      } else if (liveVehicle.flight_mode) {
        missionPhase = liveVehicle.flight_mode;
      }

      if (missionPhase === "COMPLETED" || missionPhase === "FAILED" || missionPhase === "ABORTED") {
        telemetryState = "ENDED";
      } else if (ageSeconds > 5.0) {
        telemetryState = "STALE";
      } else if (ageSeconds > 2.0) {
        telemetryState = "DEGRADED";
      }
    }

    return {
      mission,
      polyPoints,
      startPos,
      survivors,
      liveVehicle,
      livePos,
      trail: trailCoords,
      telemetryState,
      ageSeconds,
      missionPhase,
    };
  }, [mission, vehicles, targets, trails, now]);
}
