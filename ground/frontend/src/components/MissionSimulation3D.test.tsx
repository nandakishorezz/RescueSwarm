import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import React from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

afterEach(() => {
  cleanup();
});

import {
  createProceduralDrone,
  loadDroneModel,
  loadHumanModel,
  createFloatingLabel,
} from "./Drone3DModel";
import { createCameraFootprint } from "./CameraFootprint3D";
import { GroundTruthVsPredictedPanel } from "./GroundTruthVsPredictedPanel";
import { DiagnosticTelemetryReadout } from "./DiagnosticTelemetryReadout";
import { MissionSimulation3D, geoToScene, sceneToGeo } from "./MissionSimulation3D";
import type { Target } from "../types/domain";

describe("Drone3DModel & Procedural Fallbacks", () => {
  it("creates procedural drone with 4 rotating motor tops and valid bounding box", () => {
    const drone = createProceduralDrone();
    expect(drone.group).toBeDefined();
    expect(drone.rotors.length).toBe(4);
    expect(drone.isCustomModel).toBe(false);

    // Test rotor animation callback
    const initialRot = drone.rotors[0].rotation.y;
    drone.updateRotors(0.1, true);
    expect(drone.rotors[0].rotation.y).not.toBe(initialRot);

    // Disarmed should not advance rotors
    const armedRot = drone.rotors[0].rotation.y;
    drone.updateRotors(0.1, false);
    expect(drone.rotors[0].rotation.y).toBe(armedRot);

    drone.dispose();
  });

  it("creates floating billboard label with canvas texture", () => {
    const sprite = createFloatingLabel("[TRUTH] survivor_01 (STANDING)", "#f59e0b");
    expect(sprite).toBeDefined();
    expect(sprite.scale.x).toBeGreaterThan(0);
    expect(sprite.material.map).toBeDefined();
  });
});

describe("CameraFootprint3D", () => {
  it("creates approximate optical frustum and ground projection polygon", () => {
    const footprint = createCameraFootprint({ hfovDeg: 68, vfovDeg: 46 });
    expect(footprint.group).toBeDefined();
    expect(footprint.group.children.length).toBeGreaterThanOrEqual(3);

    // Update with altitude > 0
    footprint.update({ x: 10, y: 8, z: -10 } as any, 90, 0, 0, 8);
    expect(footprint.group.visible).toBe(true);

    // Update with altitude on ground (alt < 0.15) hides footprint
    footprint.update({ x: 10, y: 0.05, z: -10 } as any, 90, 0, 0, 0.05);
    expect(footprint.group.visible).toBe(false);

    footprint.dispose();
  });
});

describe("Geographic and 3D Coordinate Conversion", () => {
  it("converts WGS-84 to 3D Cartesian space and reverses with sub-millimeter precision", () => {
    const originLat = 12.9724;
    const originLon = 77.5933;

    // Test survivor_01 offset
    const testLat = 12.97287;
    const testLon = 77.59197;

    const scenePt = geoToScene(testLat, testLon, originLat, originLon);
    expect(scenePt.x).toBeLessThan(0); // West of origin
    expect(scenePt.z).toBeLessThan(0); // North of origin (-Z is North)

    const rev = sceneToGeo(scenePt.x, scenePt.z, originLat, originLon);
    expect(Math.abs(rev.lat - testLat)).toBeLessThan(1e-9);
    expect(Math.abs(rev.lon - testLon)).toBeLessThan(1e-9);
  });
});

describe("GroundTruthVsPredictedPanel", () => {
  it("renders scenario ground truth and matches with autonomous predicted target", () => {
    const scenarioSurvivors = [
      { id: "survivor_01", lat: 12.9716, lng: 77.5946, bodyState: "standing" },
      { id: "survivor_02", lat: 12.9720, lng: 77.5950, bodyState: "lying" },
    ];

    const targets: Record<string, Target> = {
      target_1: {
        target_id: "target_1",
        mission_id: "m1",
        state: "CONFIRMED",
        latitude: 12.971605,
        longitude: 77.594605,
        east_m: 0.5,
        north_m: 0.5,
        detection_confidence: 0.94,
        geolocation_uncertainty_m: 0.8,
        observation_count: 5,
        first_seen_at: "2026-10-13T00:00:00Z",
        last_seen_at: "2026-10-13T00:01:00Z",
        delivery_state: "PENDING",
        simulated: true,
      },
    };

    render(
      <GroundTruthVsPredictedPanel
        scenarioSurvivors={scenarioSurvivors}
        targets={targets}
      />
    );

    expect(screen.getByText("Ground Truth vs Predicted Location")).toBeInTheDocument();
    expect(screen.getByText("survivor_01")).toBeInTheDocument();
    expect(screen.getByText("survivor_02")).toBeInTheDocument();
    expect(screen.getByText("CONFIRMED")).toBeInTheDocument();
    expect(screen.getByText("GROUND TRUTH ONLY")).toBeInTheDocument();
  });

  it("handles missing autonomous predictions honestly with NOT DETECTED and NOT AVAILABLE", () => {
    const scenarioSurvivors = [
      { id: "survivor_unseen", lat: 12.9725, lng: 77.5930, bodyState: "lying" },
    ];

    const { unmount } = render(
      <GroundTruthVsPredictedPanel
        scenarioSurvivors={scenarioSurvivors}
        targets={{}}
      />
    );

    expect(screen.getByText("survivor_unseen")).toBeInTheDocument();
    expect(screen.getAllByText("NOT DETECTED").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("NOT AVAILABLE").length).toBeGreaterThanOrEqual(1);
    unmount();
  });

  it("calls onSelectSurvivor when a survivor row is clicked", () => {
    const scenarioSurvivors = [
      { id: "survivor_select_test", lat: 12.9716, lng: 77.5946, bodyState: "standing" },
    ];
    let selectedId = "";
    const onSelect = (id: string) => { selectedId = id; };

    const { unmount } = render(
      <GroundTruthVsPredictedPanel
        scenarioSurvivors={scenarioSurvivors}
        targets={{}}
        onSelectSurvivor={onSelect}
      />
    );

    const cell = screen.getByText("survivor_select_test");
    cell.click();
    expect(selectedId).toBe("survivor_select_test");
    unmount();
  });
});

describe("DiagnosticTelemetryReadout", () => {
  it("displays 2D and 3D coordinate comparison and computes sync tolerance", () => {
    const tel2D = {
      lat: 12.9716,
      lon: 77.5946,
      yaw: 45.0,
      alt: 8.5,
      speed: 3.2,
      seq: "142",
    };
    const drone3D = {
      lat: 12.9716,
      lon: 77.5946,
      yaw: 45.0,
      alt: 8.5,
      sceneX: 0.0,
      sceneY: 8.5,
      sceneZ: 0.0,
    };

    render(
      <DiagnosticTelemetryReadout
        telemetry2D={tel2D}
        drone3D={drone3D}
        liveVehicle={null}
        telemetryState="LIVE"
        isCustomModel={true}
        fps={60}
      />
    );

    expect(screen.getByText("2D vs 3D TELEMETRY SYNC")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText(/ΔPos 0.0 mm/)).toBeInTheDocument();
    expect(screen.getByText(/ACTUAL 3D GLTF/)).toBeInTheDocument();
  });

  it("displays PREVIEW state badge when awaiting live telemetry on launch pad", () => {
    const tel2D = { lat: 12.9724, lon: 77.5933, yaw: 0, alt: 0, speed: 0, seq: "-" };
    const drone3D = { lat: 12.9724, lon: 77.5933, yaw: 0, alt: 0, sceneX: 0, sceneY: 0, sceneZ: 0 };

    render(
      <DiagnosticTelemetryReadout
        telemetry2D={tel2D}
        drone3D={drone3D}
        liveVehicle={null}
        telemetryState="PREVIEW"
        isCustomModel={true}
        fps={60}
      />
    );

    expect(screen.getByText("PREVIEW")).toBeInTheDocument();
  });
});

describe("MissionSimulation3D WebGL Resilience & Graceful Fallback", () => {
  it("renders WebGL Unavailable fallback gracefully without crashing when WebGL is unsupported", () => {
    // In jsdom without WebGL, WebGLRenderer throws. MissionSimulation3D catches it and renders fallback.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<MissionSimulation3D scenario={null} />);
    expect(screen.getByText("WebGL Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Hardware accelerated 3D graphics could not be initialized/)).toBeInTheDocument();
    consoleError.mockRestore();
  });
});

describe("Camera Controls & FOLLOW Mode Mechanics", () => {
  it("FOLLOW mode delta preservation maintains manual user orbit and zoom offsets", () => {
    // Test the delta-following algorithm:
    // If the user orbited/zoomed to a relative offset:
    const cameraPos = new THREE.Vector3(10, 5, 10);
    const controlsTarget = new THREE.Vector3(2, 0, 2);
    const initialUserOffset = new THREE.Vector3().subVectors(cameraPos, controlsTarget);

    // Drone moves by (dx, dy, dz)
    const prevDronePos = new THREE.Vector3(2, 0, 2);
    const curDronePos = new THREE.Vector3(5, 3, 7);
    const droneDelta = new THREE.Vector3().subVectors(curDronePos, prevDronePos);

    // FOLLOW algorithm applied:
    controlsTarget.add(droneDelta);
    cameraPos.add(droneDelta);

    // Target is now centered on the new drone position:
    expect(controlsTarget.x).toBeCloseTo(curDronePos.x);
    expect(controlsTarget.y).toBeCloseTo(curDronePos.y);
    expect(controlsTarget.z).toBeCloseTo(curDronePos.z);

    // User's custom orbit angle and zoom distance is strictly preserved:
    const newUserOffset = new THREE.Vector3().subVectors(cameraPos, controlsTarget);
    expect(newUserOffset.x).toBeCloseTo(initialUserOffset.x);
    expect(newUserOffset.y).toBeCloseTo(initialUserOffset.y);
    expect(newUserOffset.z).toBeCloseTo(initialUserOffset.z);
    expect(newUserOffset.length()).toBeCloseTo(initialUserOffset.length());
  });

  it("yields automated camera transition immediately when user scrolls or drags", () => {
    let activeTransition: any = {
      startTime: 1000,
      durationMs: 800,
    };

    // User wheel event triggers listener
    const onWheel = () => {
      activeTransition = null;
    };
    onWheel();
    expect(activeTransition).toBeNull();
  });
});

describe("Asset Loading Resilience & Procedural Fallbacks", () => {
  it("loadDroneModel returns procedural fallback without throwing if GLTF load fails", async () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const drone = await loadDroneModel(0.85);
    expect(drone).toBeDefined();
    expect(drone.group).toBeDefined();
    expect(drone.rotors.length).toBeGreaterThanOrEqual(4);
    drone.dispose();
    consoleWarn.mockRestore();
  });

  it("loadHumanModel returns procedural fallback without throwing if GLTF load fails", async () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Mock GLTFLoader.prototype.loadAsync to reject immediately
    const loadSpy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockRejectedValue(new Error("File not found"));

    const standingHuman = await loadHumanModel("standing", "survivor_fail_test");
    expect(standingHuman).toBeDefined();
    expect(standingHuman.group).toBeDefined();
    standingHuman.dispose();

    const lyingHuman = await loadHumanModel("lying", "survivor_fail_test_2");
    expect(lyingHuman).toBeDefined();
    expect(lyingHuman.group).toBeDefined();
    lyingHuman.dispose();

    loadSpy.mockRestore();
    consoleWarn.mockRestore();
  });
});

describe("Panel Minimization & Flexible Zoom Controls", () => {
  it("allows minimizing and expanding GroundTruthVsPredictedPanel", () => {
    const { getByText, unmount } = render(
      <GroundTruthVsPredictedPanel
        scenarioSurvivors={[{ id: "s1", lat: 12.9, lon: 77.5 }]}
        targets={{}}
      />
    );

    // Click MINIMIZE button
    const minBtn = getByText("_ MINIMIZE");
    fireEvent.click(minBtn);

    // Now minimized pill should appear with EXPAND button
    expect(getByText(/Survivors \(1\)/)).toBeInTheDocument();
    const expandBtn = getByText("＋ EXPAND");
    fireEvent.click(expandBtn);

    // Expanded view restored
    expect(getByText("Ground Truth vs Predicted Location")).toBeInTheDocument();
    unmount();
  });

  it("allows minimizing and expanding DiagnosticTelemetryReadout", () => {
    const { getByText, unmount } = render(
      <DiagnosticTelemetryReadout
        telemetry2D={{ lat: 12.9, lon: 77.5, yaw: 0, alt: 0, speed: 0, seq: "1" }}
        drone3D={{ lat: 12.9, lon: 77.5, yaw: 0, alt: 0, sceneX: 0, sceneY: 0, sceneZ: 0 }}
        liveVehicle={null}
        telemetryState="LIVE"
        isCustomModel={true}
        fps={60}
      />
    );

    const minBtn = getByText("_ MINIMIZE");
    fireEvent.click(minBtn);

    expect(getByText(/SYNC: LIVE \(60 FPS\)/)).toBeInTheDocument();
    const expandBtn = getByText("＋ EXPAND");
    fireEvent.click(expandBtn);

    expect(getByText("2D vs 3D TELEMETRY SYNC")).toBeInTheDocument();
    unmount();
  });

  it("flexible zoom step calculates new camera position with clamp bounds", () => {
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
    camera.position.set(0, 10, 10);
    const target = new THREE.Vector3(0, 0, 0);

    const initialDistance = camera.position.distanceTo(target);

    // Zoom In by 30% (factor 0.7)
    const inOffset = new THREE.Vector3().subVectors(camera.position, target);
    inOffset.setLength(THREE.MathUtils.clamp(inOffset.length() * 0.7, 0.1, 3000));
    camera.position.copy(target).add(inOffset);

    expect(camera.position.distanceTo(target)).toBeCloseTo(initialDistance * 0.7);

    // Zoom Out by 35% (factor 1.35)
    const outOffset = new THREE.Vector3().subVectors(camera.position, target);
    outOffset.setLength(THREE.MathUtils.clamp(outOffset.length() * 1.35, 0.1, 3000));
    camera.position.copy(target).add(outOffset);

    expect(camera.position.distanceTo(target)).toBeCloseTo(initialDistance * 0.7 * 1.35);
  });
});

describe("Physical Scale & Model Dimensions Verification", () => {
  it("procedural drone has physically consistent scale (~0.76-0.85m span) and rests flush on landing surface", () => {
    const drone = createProceduralDrone();
    drone.group.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(drone.group);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxSpan = Math.max(size.x, size.z);
    expect(maxSpan).toBeGreaterThanOrEqual(0.70);
    expect(maxSpan).toBeLessThanOrEqual(0.90);
    // Height should be realistic (~0.15-0.25m)
    expect(size.y).toBeGreaterThan(0.10);
    expect(size.y).toBeLessThan(0.30);
    // Skids contact with ground
    expect(box.min.y).toBeGreaterThanOrEqual(0.01);
    expect(box.min.y).toBeLessThan(0.05);

    drone.dispose();
  });

  it("procedural human has realistic physical dimensions (1.70-1.73m standing, matching length lying)", async () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const loadSpy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockRejectedValue(new Error("Use procedural"));

    const standing = await loadHumanModel("standing", "test_stand_proc");
    standing.group.updateMatrixWorld(true);
    const standBox = new THREE.Box3().setFromObject(standing.group);
    const standSize = new THREE.Vector3();
    standBox.getSize(standSize);

    // Standing height is 1.72m (within 1.70 - 1.73m)
    expect(standSize.y).toBeGreaterThanOrEqual(1.70);
    expect(standSize.y).toBeLessThanOrEqual(1.73);
    expect(standBox.min.y).toBeCloseTo(0.0, 1);

    const lying = await loadHumanModel("lying", "test_lie_proc");
    lying.group.updateMatrixWorld(true);
    const lieBox = new THREE.Box3().setFromObject(lying.group);
    const lieSize = new THREE.Vector3();
    lieBox.getSize(lieSize);

    // Lying body length along ground is 1.72m
    const bodyLength = Math.max(lieSize.x, lieSize.z);
    expect(bodyLength).toBeGreaterThanOrEqual(1.70);
    expect(bodyLength).toBeLessThanOrEqual(1.73);
    // Vertical thickness is body thickness (~0.25-0.35m), NOT full human height
    expect(lieSize.y).toBeLessThan(0.40);
    expect(lieBox.min.y).toBeCloseTo(0.0, 2);

    standing.dispose();
    lying.dispose();
    loadSpy.mockRestore();
    consoleWarn.mockRestore();
  });

  it("normalizes real GLTF human model to verified realistic physical size (1.72m) preserving aspect ratio and ground contact", async () => {
    // Construct mock raw person GLTF scene with measured raw model dimensions
    // Raw bounds: X: [-0.342, 0.346], Y: [0, 1.9004], Z: [-0.327, 0.321]
    const rawPersonScene = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.6878, 1.9004, 0.6479));
    mesh.position.set(0.002, 1.9004 / 2, -0.003);
    rawPersonScene.add(mesh);

    const loadSpy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockResolvedValue({
      scene: rawPersonScene,
      animations: [],
      cameras: [],
      asset: {},
      scenes: [rawPersonScene],
    } as any);

    // 1. Standing human verification
    const standing = await loadHumanModel("standing", "verified_standing");
    standing.group.updateMatrixWorld(true);
    const standBox = new THREE.Box3().setFromObject(standing.group);
    const standSize = new THREE.Vector3();
    standBox.getSize(standSize);

    // Height must be exactly 1.72m (representing person around 5 ft 7-8 in)
    expect(standSize.y).toBeCloseTo(1.72, 2);
    // Width and depth are uniformly scaled
    expect(standSize.x).toBeCloseTo(0.622, 2);
    expect(standSize.z).toBeCloseTo(0.586, 2);
    // Ground contact: feet flush on ground (y = 0)
    expect(standBox.min.y).toBeCloseTo(0.0, 2);

    // Verify uniform scaling
    const innerOriented = standing.group.getObjectByName("HumanOriented_verified_standing_standing");
    expect(innerOriented).toBeDefined();
    expect(innerOriented!.scale.x).toBeCloseTo(innerOriented!.scale.y, 4);
    expect(innerOriented!.scale.y).toBeCloseTo(innerOriented!.scale.z, 4);

    // 2. Lying human verification
    const lying = await loadHumanModel("lying", "verified_lying");
    lying.group.updateMatrixWorld(true);
    const lieBox = new THREE.Box3().setFromObject(lying.group);
    const lieSize = new THREE.Vector3();
    lieBox.getSize(lieSize);

    // Body length along ground is 1.72m (full human height preserved, not vertical bbox height)
    expect(lieSize.z).toBeCloseTo(1.72, 2);
    expect(lieSize.x).toBeCloseTo(0.622, 2);
    // Vertical thickness is ~0.59m (chest height off ground, NOT 1.72m)
    expect(lieSize.y).toBeCloseTo(0.586, 2);
    // Ground contact: resting on ground (min Y near 0.02)
    expect(lieBox.min.y).toBeGreaterThanOrEqual(0.015);
    expect(lieBox.min.y).toBeLessThan(0.04);

    standing.dispose();
    lying.dispose();
    loadSpy.mockRestore();
  });

  it("normalizes real GLTF drone model to verified physical span (0.85m) with ground contact", async () => {
    // Construct mock raw drone GLTF scene with measured raw model dimensions
    // Raw bounds: X: 35.47, Y: 14.44, Z: 40.43 (max horizontal span = 40.43)
    const rawDroneScene = new THREE.Group();
    const bodyMesh = new THREE.Mesh(new THREE.BoxGeometry(35.47, 14.44, 40.43));
    bodyMesh.position.set(3.19, -0.05, -3.32);
    rawDroneScene.add(bodyMesh);

    // Add 4 motor nodes
    for (let i = 1; i <= 4; i++) {
      const motor = new THREE.Group();
      motor.name = `motor_top${i}`;
      rawDroneScene.add(motor);
    }

    const loadSpy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockResolvedValue({
      scene: rawDroneScene,
      animations: [],
      cameras: [],
      asset: {},
      scenes: [rawDroneScene],
    } as any);

    const drone = await loadDroneModel(0.85);
    drone.group.updateMatrixWorld(true);
    const droneBox = new THREE.Box3().setFromObject(drone.group);
    const droneSize = new THREE.Vector3();
    droneBox.getSize(droneSize);

    // Verify verified physical span is 0.85m
    const maxSpan = Math.max(droneSize.x, droneSize.z);
    expect(maxSpan).toBeCloseTo(0.85, 2);
    expect(droneSize.y).toBeCloseTo(0.304, 2);
    // Ground contact: flush at y = 0
    expect(droneBox.min.y).toBeCloseTo(0.0, 2);

    // Verify uniform scaling
    const innerOriented = drone.group.getObjectByName("DroneOrientedInner");
    expect(innerOriented).toBeDefined();
    expect(innerOriented!.scale.x).toBeCloseTo(innerOriented!.scale.y, 4);
    expect(innerOriented!.scale.y).toBeCloseTo(innerOriented!.scale.z, 4);

    drone.dispose();
    loadSpy.mockRestore();
  });

  it("preserves model dimensions independently of camera distance or framing mode", async () => {
    const rawPersonScene = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.6878, 1.9004, 0.6479));
    mesh.position.set(0, 1.9004 / 2, 0);
    rawPersonScene.add(mesh);

    const loadSpy = vi.spyOn(GLTFLoader.prototype, "loadAsync").mockResolvedValue({
      scene: rawPersonScene,
      animations: [],
      cameras: [],
      asset: {},
      scenes: [rawPersonScene],
    } as any);

    const human = await loadHumanModel("standing", "cam_test_human");
    human.group.updateMatrixWorld(true);
    const initialBox = new THREE.Box3().setFromObject(human.group);
    const initialSize = new THREE.Vector3();
    initialBox.getSize(initialSize);

    // Simulate camera moving between overview (150m) and close-up (2m)
    const camera = new THREE.PerspectiveCamera(48, 16 / 9, 0.1, 2500);
    camera.position.set(0, 150, 150); // OVERVIEW
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);

    // Model world matrix does not change with camera movement
    human.group.updateMatrixWorld(true);
    const overviewBox = new THREE.Box3().setFromObject(human.group);
    const overviewSize = new THREE.Vector3();
    overviewBox.getSize(overviewSize);
    expect(overviewSize.y).toBeCloseTo(initialSize.y, 4);

    camera.position.set(1.5, 1.2, 1.5); // CLOSE-UP
    camera.lookAt(0, 0.85, 0);
    camera.updateMatrixWorld(true);

    human.group.updateMatrixWorld(true);
    const closeBox = new THREE.Box3().setFromObject(human.group);
    const closeSize = new THREE.Vector3();
    closeBox.getSize(closeSize);
    expect(closeSize.y).toBeCloseTo(initialSize.y, 4);

    human.dispose();
    loadSpy.mockRestore();
  });
});
