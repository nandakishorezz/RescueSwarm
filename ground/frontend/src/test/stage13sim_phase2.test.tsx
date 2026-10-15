import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SimulationPage, v3ActorYawDeg } from "../pages/SimulationPage";
import { api } from "../services/api";
import { useGcsStore } from "../stores/gcsStore";

vi.mock("../components/SimulationWorldPreview", () => ({
  SimulationWorldPreview: () => <div role="img" aria-label="Simulation configuration 3D preview" />,
}));

const profiles = {
  drone: { profile_id: "reference", rotor_layout: "QUAD_X", rotor_count: 4, mass_kg: { value: 1.5 }, arm_length_m: { value: .255 } },
  camera: { camera_id: "ov9281", resolution_width: 1280, resolution_height: 800, fps: { value: 30 }, intrinsics: { fx: { value: 800 } }, mount_translation_body_frd: { value: [0, 0, .08] } },
  environment: { profile_id: "moderate", random_seed: 84521, wind: { mode: "CONSTANT", mean_speed_mps: { value: 1.5 }, mean_direction_deg: { value: 90 } } },
  sensor_profiles: {
    REFERENCE_NOMINAL: {
      gps: { horizontal_noise_m: .8, update_rate_hz: 5 },
      imu: { gyro_motor_noise: .02, accelerometer_motor_noise: .06 },
      compass: { noise_mgauss: 2 }, barometer: { noise_m: .3 },
      camera: { latency_ms: 100, jitter_ms: 8, gaussian_noise_stddev_dn: 2.5, exposure_preset: "NORMAL_DAY" },
    },
  },
};

beforeEach(() => {
  useGcsStore.setState({ mode: "SIMULATION", competitionMode: false, mission: null, coveragePlan: null });
  vi.spyOn(api, "simulationReferenceProfiles").mockResolvedValue(profiles as never);
  vi.spyOn(api, "simulationStatus").mockResolvedValue({ state: "IDLE", config_locked: false } as never);
  vi.spyOn(api, "simulationPerceptionLatest").mockResolvedValue({
    frame: { frame_id: "camera:42", resolution: [1280, 800], model_id: "yolo11n_person_onnx" },
    vision: { detector_fps: 4.5, dropped_frames: 2, source_metrics: { simulation_time_source_fps: 30.3 } },
    result: { confirmed_targets: 2, frames_inferred: 120, raw_person_detections: 9, geolocated_observations: 7 },
  } as never);
  vi.spyOn(api, "simulationPerceptionObserver").mockResolvedValue({} as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Stage 13SIM Phase 2 operator surface", () => {
  it("uses the V3-qualified actor yaw without rotating the proven lying pose", () => {
    expect(v3ActorYawDeg("standing")).toBe(-90);
    expect(v3ActorYawDeg("sitting")).toBe(-90);
    expect(v3ActorYawDeg("lying")).toBe(0);
  });

  it("shows the actual virtual-camera and detector panels as separate views", async () => {
    render(<SimulationPage />);
    const panels = within(screen.getByRole("navigation", { name: "Simulation laboratory panels" }));
    fireEvent.click(panels.getByRole("button", { name: "CAMERA" }));
    expect(await screen.findByText("ACTUAL MONO8 AUTONOMY INPUT")).toBeInTheDocument();
    expect(screen.getByAltText("Latest monochrome Gazebo autonomy-camera frame")).toBeInTheDocument();
    fireEvent.click(panels.getByRole("button", { name: "PERCEPTION" }));
    expect(screen.getByText("NO TRUTH FILTERING")).toBeInTheDocument();
    expect(screen.getByText("yolo11n_person_onnx")).toBeInTheDocument();
  });

  it("keeps observer truth hidden until the explicit observer control is enabled", async () => {
    render(<SimulationPage />);
    const panels = within(screen.getByRole("navigation", { name: "Simulation laboratory panels" }));
    fireEvent.click(panels.getByRole("button", { name: "OBSERVER" }));
    expect(await screen.findByText(/GROUND TRUTH IS HIDDEN/)).toBeInTheDocument();
    expect(api.simulationPerceptionObserver).not.toHaveBeenCalled();
  });

  it("exposes provenance-labelled sensor realism controls that lock with the run", async () => {
    render(<SimulationPage />);
    fireEvent.click(await screen.findByRole("button", { name: "SENSORS" }));
    expect(screen.getByText("Sensor Realism")).toBeInTheDocument();
    expect(screen.getByText(/PHYSICAL UNVERIFIED/)).toBeInTheDocument();
    expect(screen.getByLabelText("Sensor realism profile")).toHaveValue("REFERENCE_NOMINAL");
    expect(screen.getByLabelText("Sensor seed")).not.toBeDisabled();
  });
});
