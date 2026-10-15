import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FleetMap, appendFleetTrails } from "./FleetMap";
import type { FleetVehicleView } from "./types";

const vehicle = (id: string, latitude: number): FleetVehicleView => ({
  vehicle_id: id, role: id === "drone_01" ? "SEARCHER" : "SERVICER", system_id: id === "drone_01" ? 1 : 2,
  connection: "tcp:127.0.0.1:5760", state: "SEARCHING", connected: true, telemetry_age_s: 0,
  telemetry: { latitude, longitude: 77.5, relative_altitude_m: 10, ground_speed_m_s: 4,
    flight_mode: "AUTO", armed: true, gps_fix_type: 3 }, progress_percent: 5,
  camera_status: "LIVE", camera_frame_count: 1, camera_decoded_count: 1,
  camera_inference_count: 1, camera_age_s: 0, last_event: null,
});

describe("fleet presentation", () => {
  it("keeps each real vehicle trail separate and idempotent on repeated polls", () => {
    const first = appendFleetTrails({}, [vehicle("drone_01", 12.1), vehicle("drone_02", 12.2)]);
    expect(Object.keys(first)).toHaveLength(2);
    expect(appendFleetTrails(first, [vehicle("drone_01", 12.1)]).drone_01).toHaveLength(1);
    const next = appendFleetTrails(first, [vehicle("drone_01", 12.1001)]);
    expect(next.drone_01).toHaveLength(2);
    expect(next.drone_02).toHaveLength(1);
    render(<FleetMap vehicles={[vehicle("drone_01", 12.1001), vehicle("drone_02", 12.2)]} trails={next} />);
    expect(screen.getByRole("img", { name: "Live fleet GPS trails" })).toBeInTheDocument();
    expect(screen.getByText("drone_01")).toBeInTheDocument();
    expect(screen.getByText("drone_02")).toBeInTheDocument();
  });
});
