import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModeNav } from "../components/ModeNav";
import { SimulationPage } from "../pages/SimulationPage";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

vi.mock("../components/SimulationWorldPreview", () => ({
  SimulationWorldPreview: () => <div role="img" aria-label="Simulation configuration 3D preview" />,
}));

const profiles = {
  drone: { profile_id: "reference", rotor_layout: "QUAD_X", rotor_count: 4, mass_kg: { value: 1.5 }, arm_length_m: { value: .255 }, cruise_speed_mps: { value: 4 } },
  camera: { camera_id: "camera", resolution_width: 1280, resolution_height: 800, fps: { value: 30 }, intrinsics: { fx: { value: 800 } }, mount_translation_body_frd: { value: [0, 0, .08] } },
  environment: { profile_id: "wind", random_seed: 84521, wind: { mode: "GUSTING", mean_speed_mps: { value: 3 }, mean_direction_deg: { value: 90 } } },
};

beforeEach(() => {
  useGcsStore.setState({ mode: "SIMULATION", competitionMode: false, mission: null, coveragePlan: null });
  vi.spyOn(api, "simulationReferenceProfiles").mockResolvedValue(profiles as never);
  vi.spyOn(api, "simulationStatus").mockResolvedValue({ state: "IDLE", config_locked: false } as never);
  vi.spyOn(api, "simulationPerceptionLatest").mockRejectedValue(new Error("No run"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Stage 13SIM Phase 1 laboratory isolation", () => {
  it("labels simulation evidence and exposes builders without creating autonomy targets", async () => {
    render(<SimulationPage />);
    expect(screen.getByRole("heading", { name: "SIMULATION LAB" })).toBeInTheDocument();
    expect(screen.getByText("NO PHYSICAL AIRCRAFT")).toBeInTheDocument();
    expect(screen.getByText("H1 PENDING")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Drone Builder" })).toBeInTheDocument();
    expect(screen.queryByText(/CONFIRMED TARGET/)).not.toBeInTheDocument();
    expect(await screen.findByText(/Reference profiles loaded/)).toBeInTheDocument();
  });

  it("removes the Simulation laboratory from Competition Mode", () => {
    useGcsStore.setState({ competitionMode: true });
    render(<ModeNav />);
    expect(screen.queryByRole("button", { name: "SIMULATION" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "ENGINEERING" })).not.toBeInTheDocument();
  });
});
