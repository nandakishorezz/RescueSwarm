import React, { useEffect, useRef, useState, useMemo, useCallback } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { useVisualizationModel } from "./useVisualizationModel";
import { useGcsStore } from "../stores/gcsStore";
import {
  loadDroneModel,
  loadHumanModel,
  createFloatingLabel,
  createTerrainTexture,
  createMedicalKitModel,
  createParachuteModel,
  type LoadedDrone,
  type LoadedHuman,
} from "./Drone3DModel";
import { createCameraFootprint, type CameraFootprintVisualizer } from "./CameraFootprint3D";
import { DiagnosticTelemetryReadout } from "./DiagnosticTelemetryReadout";
import { GroundTruthVsPredictedPanel } from "./GroundTruthVsPredictedPanel";
import type { VehicleState, Target } from "../types/domain";
import "./MissionSimulation3D.css";

export type CameraMode = "FOLLOW" | "ORBIT" | "TOP_DOWN" | "OVERVIEW";

interface Props {
  scenario?: any;
}

const EARTH_RADIUS_M = 6378137;

/**
 * Standard WGS-84 Flat-Earth Projection to 3D Cartesian coordinates.
 * Origin is at (0, 0, 0) [Mission launch position].
 * +X = East (meters)
 * +Y = Altitude (meters, Up)
 * -Z = North (meters)
 */
export function geoToScene(
  lat: number,
  lon: number,
  originLat: number,
  originLon: number
): { x: number; z: number } {
  const northM = ((lat - originLat) * Math.PI / 180) * EARTH_RADIUS_M;
  const eastM = ((lon - originLon) * Math.PI / 180) * EARTH_RADIUS_M * Math.cos((originLat * Math.PI) / 180);
  return { x: eastM, z: -northM };
}

export function sceneToGeo(
  x: number,
  z: number,
  originLat: number,
  originLon: number
): { lat: number; lon: number } {
  const northM = -z;
  const eastM = x;
  const lat = originLat + (northM / EARTH_RADIUS_M) * (180 / Math.PI);
  const lon = originLon + (eastM / (EARTH_RADIUS_M * Math.cos((originLat * Math.PI) / 180))) * (180 / Math.PI);
  return { lat, lon };
}

/**
 * Point-in-polygon ray casting test.
 */
function pointInPolygon(px: number, pz: number, poly: { x: number; z: number }[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x, zi = poly[i].z;
    const xj = poly[j].x, zj = poly[j].z;
    const intersect = ((zi > pz) !== (zj > pz)) &&
      (px < (xj - xi) * (pz - zi) / (zj - zi + 1e-12) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Clips standard metric grid lines strictly to the polygon boundary.
 */
function generateClippedGridLines(
  poly: { x: number; z: number }[],
  gridSpacing = 5
): THREE.Vector3[] {
  if (poly.length < 3) return [];
  const segments: THREE.Vector3[] = [];

  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const p of poly) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.z < minZ) minZ = p.z;
    if (p.z > maxZ) maxZ = p.z;
  }

  const startX = Math.floor(minX / gridSpacing) * gridSpacing;
  const endX = Math.ceil(maxX / gridSpacing) * gridSpacing;
  const startZ = Math.floor(minZ / gridSpacing) * gridSpacing;
  const endZ = Math.ceil(maxZ / gridSpacing) * gridSpacing;

  // 1. Vertical grid lines (constant X, along Z)
  for (let x = startX; x <= endX; x += gridSpacing) {
    const hits: number[] = [];
    for (let i = 0; i < poly.length; i++) {
      const p1 = poly[i];
      const p2 = poly[(i + 1) % poly.length];
      if ((p1.x <= x && p2.x >= x) || (p2.x <= x && p1.x >= x)) {
        if (Math.abs(p2.x - p1.x) > 1e-6) {
          const t = (x - p1.x) / (p2.x - p1.x);
          hits.push(p1.z + t * (p2.z - p1.z));
        }
      }
    }
    hits.sort((a, b) => a - b);
    for (let h = 0; h < hits.length - 1; h += 2) {
      const z1 = hits[h];
      const z2 = hits[h + 1];
      const midZ = (z1 + z2) / 2;
      if (pointInPolygon(x, midZ, poly)) {
        segments.push(new THREE.Vector3(x, 0.035, z1));
        segments.push(new THREE.Vector3(x, 0.035, z2));
      }
    }
  }

  // 2. Horizontal grid lines (constant Z, along X)
  for (let z = startZ; z <= endZ; z += gridSpacing) {
    const hits: number[] = [];
    for (let i = 0; i < poly.length; i++) {
      const p1 = poly[i];
      const p2 = poly[(i + 1) % poly.length];
      if ((p1.z <= z && p2.z >= z) || (p2.z <= z && p1.z >= z)) {
        if (Math.abs(p2.z - p1.z) > 1e-6) {
          const t = (z - p1.z) / (p2.z - p1.z);
          hits.push(p1.x + t * (p2.x - p1.x));
        }
      }
    }
    hits.sort((a, b) => a - b);
    for (let h = 0; h < hits.length - 1; h += 2) {
      const x1 = hits[h];
      const x2 = hits[h + 1];
      const midX = (x1 + x2) / 2;
      if (pointInPolygon(midX, z, poly)) {
        segments.push(new THREE.Vector3(x1, 0.035, z));
        segments.push(new THREE.Vector3(x2, 0.035, z));
      }
    }
  }

  return segments;
}

interface CameraTransition {
  startPos: THREE.Vector3;
  targetPos: THREE.Vector3;
  startLookAt: THREE.Vector3;
  targetLookAt: THREE.Vector3;
  startTime: number;
  durationMs: number;
}

export function MissionSimulation3D({ scenario }: Props) {
  const [now, setNow] = useState(performance.now());
  const canvasContainerRef = useRef<HTMLDivElement>(null);
  const [cameraMode, setCameraMode] = useState<CameraMode>("FOLLOW");
  const [showcaseActive, setShowcaseActive] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(true);
  const [showGtPanel, setShowGtPanel] = useState(true);
  const [isCustomModel, setIsCustomModel] = useState(true);
  const [fps, setFps] = useState(60);
  const [selectedSurvivorId, setSelectedSurvivorId] = useState<string | null>(null);
  const [webGlAvailable, setWebGlAvailable] = useState(true);

  // Throttle React state update for freshness to 1Hz
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const model = useVisualizationModel(scenario, now);
  const targets = useGcsStore((state) => state.targets);
  const telemetryBufferRef = useRef<VehicleState[]>([]);

  // Origin reference locked strictly to the mission launch position
  const origin = useMemo(() => {
    const lat =
      scenario?.startPosition?.lat ??
      scenario?.startPosition?.latitude ??
      model.startPos?.lat ??
      (model.polyPoints.length > 0 ? model.polyPoints[0].lat : 12.9724);
    const lon =
      scenario?.startPosition?.lng ??
      scenario?.startPosition?.lon ??
      scenario?.startPosition?.longitude ??
      model.startPos?.lon ??
      (model.polyPoints.length > 0 ? model.polyPoints[0].lon : 77.5933);
    return { lat, lon };
  }, [scenario?.startPosition, model.startPos, model.polyPoints]);

  const originRef = useRef(origin);
  originRef.current = origin;

  // Diagnostic state for HUD readout (throttled updates to preserve 60fps responsiveness)
  const [readout2D, setReadout2D] = useState({ lat: origin.lat, lon: origin.lon, yaw: 0, alt: 0, speed: 0, seq: "-" });
  const [readout3D, setReadout3D] = useState({ lat: origin.lat, lon: origin.lon, yaw: 0, alt: 0, sceneX: 0, sceneY: 0, sceneZ: 0 });

  // Buffer live telemetry from authoritative store
  useEffect(() => {
    if (model.liveVehicle && model.liveVehicle._receive_time_ms) {
      const buffer = telemetryBufferRef.current;
      if (buffer.length === 0 || buffer[buffer.length - 1].sequence !== model.liveVehicle.sequence) {
        buffer.push(model.liveVehicle);
        if (buffer.length > 40) buffer.shift();
      }
    }
  }, [model.liveVehicle]);

  // Three.js scene refs
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const droneObjectRef = useRef<LoadedDrone | null>(null);
  const droneGroupRef = useRef<THREE.Group | null>(null);
  const dronePayloadRef = useRef<THREE.Group | null>(null);
  const dropContainerRef = useRef<THREE.Group | null>(null);
  const activeDropsRef = useRef<Map<string, {
    targetId: string;
    group: THREE.Group;
    startPos: THREE.Vector3;
    endPos: THREE.Vector3;
    startTime: number;
    durationMs: number;
  }>>(new Map());
  const completedDropsRef = useRef<Set<string>>(new Set());
  const [targetDropTick, setTargetDropTick] = useState(0);
  const groundShadowRef = useRef<THREE.Mesh | null>(null);
  const footprintRef = useRef<CameraFootprintVisualizer | null>(null);
  const trailLineRef = useRef<THREE.Line | null>(null);
  const survivorsGroupRef = useRef<THREE.Group | null>(null);
  const targetsGroupRef = useRef<THREE.Group | null>(null);
  const polygonGroupRef = useRef<THREE.Group | null>(null);
  const highlightGroupRef = useRef<THREE.Group | null>(null);

  const cameraModeRef = useRef<CameraMode>(cameraMode);
  cameraModeRef.current = cameraMode;
  const showcaseRef = useRef<boolean>(showcaseActive);
  showcaseRef.current = showcaseActive;

  // Camera transition & tracking state
  const cameraTransitionRef = useRef<CameraTransition | null>(null);
  const prevDronePosRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 0));

  // Overview bounds reference
  const overviewBoundsRef = useRef<{ center: THREE.Vector3; distance: number }>({
    center: new THREE.Vector3(0, 0, 0),
    distance: 120,
  });

  // Calculate polygon in 2D scene space
  const polyScenePoints = useMemo(() => {
    if (!model.polyPoints || model.polyPoints.length < 3) return [];
    return model.polyPoints.map((pt) => geoToScene(pt.lat, pt.lon, origin.lat, origin.lon));
  }, [model.polyPoints, origin]);

  // Serialized poly key to prevent re-generating mesh on non-geometry re-renders
  const polyKey = useMemo(() => {
    return JSON.stringify(polyScenePoints);
  }, [polyScenePoints]);

  // Memoize clipped grid lines so polygon ray-clipping runs once per polygon change
  const clippedGridSegments = useMemo(() => {
    return generateClippedGridLines(polyScenePoints, 5);
  }, [polyKey]);

  // Extract Scenario Ground Truth Survivors
  const scenarioSurvivors = useMemo(() => {
    if (scenario?.survivors && Array.isArray(scenario.survivors) && scenario.survivors.length > 0) {
      return scenario.survivors;
    }
    return (model.survivors || []).filter((s) => s.state === "SCENARIO_TRUTH");
  }, [scenario?.survivors, model.survivors]);

  // Serialized survivor and target keys to prevent rebuilding 3D models on high-frequency state updates
  const scenarioSurvivorsKey = useMemo(() => {
    return JSON.stringify(scenarioSurvivors.map((s: any) => ({
      id: s.id,
      lat: s.lat ?? s.latitude,
      lon: s.lng ?? s.lon ?? s.longitude,
      bodyState: s.bodyState,
    })));
  }, [scenarioSurvivors]);

  const targetsKey = useMemo(() => {
    const list = Object.values(targets || {});
    return JSON.stringify(list.map((t) => ({
      id: t.target_id,
      lat: t.latitude,
      lon: t.longitude,
      state: t.state,
      conf: t.detection_confidence,
      delivery_state: t.delivery_state,
      deliv_east: t.delivery_point_east_m,
      deliv_north: t.delivery_point_north_m,
    })));
  }, [targets]);

  // Compute Overview Bounds
  useEffect(() => {
    const points: { x: number; z: number }[] = [{ x: 0, z: 0 }]; // Helipad
    polyScenePoints.forEach((p) => points.push(p));
    scenarioSurvivors.forEach((s: any) => {
      const lat = s.lat ?? s.latitude;
      const lon = s.lng ?? s.lon ?? s.longitude;
      if (lat !== undefined && lon !== undefined) {
        points.push(geoToScene(lat, lon, origin.lat, origin.lon));
      }
    });

    if (points.length > 0) {
      let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
      points.forEach((p) => {
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.z < minZ) minZ = p.z;
        if (p.z > maxZ) maxZ = p.z;
      });

      const centerX = (minX + maxX) / 2;
      const centerZ = (minZ + maxZ) / 2;
      const spanX = maxX - minX;
      const spanZ = maxZ - minZ;
      const maxSpan = Math.max(spanX, spanZ, 60);

      overviewBoundsRef.current = {
        center: new THREE.Vector3(centerX, 0, centerZ),
        distance: maxSpan * 1.25,
      };
    }
  }, [polyScenePoints, scenarioSurvivors, origin]);

  // Smooth Camera Transition Helper
  const startCameraTransition = useCallback((targetPos: THREE.Vector3, targetLookAt: THREE.Vector3, durationMs = 800) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;

    cameraTransitionRef.current = {
      startPos: camera.position.clone(),
      targetPos: targetPos.clone(),
      startLookAt: controls.target.clone(),
      targetLookAt: targetLookAt.clone(),
      startTime: performance.now(),
      durationMs,
    };
  }, []);

  // Focus a specific survivor
  const handleSelectSurvivor = useCallback((id: string) => {
    setSelectedSurvivorId(id);
    const curOrigin = originRef.current;

    let targetCoords: { lat: number; lon: number } | null = null;
    const foundScenario = scenarioSurvivors.find((s: any) => s.id === id);
    if (foundScenario) {
      const lat = foundScenario.lat ?? foundScenario.latitude;
      const lon = foundScenario.lng ?? foundScenario.lon ?? foundScenario.longitude;
      if (lat !== undefined && lon !== undefined) targetCoords = { lat, lon };
    } else {
      const foundTarget = targets[id];
      if (foundTarget && foundTarget.latitude && foundTarget.longitude) {
        targetCoords = { lat: foundTarget.latitude, lon: foundTarget.longitude };
      }
    }

    if (targetCoords) {
      const pt = geoToScene(targetCoords.lat, targetCoords.lon, curOrigin.lat, curOrigin.lon);
      const isLying = foundScenario?.bodyState === "lying";
      const targetY = isLying ? 0.3 : 0.85;
      const lookAt = new THREE.Vector3(pt.x, targetY, pt.z);
      // Close inspection distance (~3.2m away) so survivor details are clearly readable
      const camPos = new THREE.Vector3(pt.x + 2.2, targetY + 1.2, pt.z + 2.2);
      startCameraTransition(camPos, lookAt, 850);
      setCameraMode("ORBIT");
    }
  }, [scenarioSurvivors, targets, startCameraTransition]);

  // Stable ref for handleSelectSurvivor so scene setup effect runs strictly ONCE on mount
  const handleSelectSurvivorRef = useRef(handleSelectSurvivor);
  handleSelectSurvivorRef.current = handleSelectSurvivor;

  // Single Three.js Scene Setup & Render Loop (Runs ONCE on mount, strictly unmounts on tear-down)
  useEffect(() => {
    const container = canvasContainerRef.current;
    if (!container) return;

    let isDisposed = false;
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 540;

    // 1. Scene & Camera
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x061512);
    scene.fog = new THREE.FogExp2(0x061512, 0.0018);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 2500);
    // Initial camera position: close inspection of drone on helipad
    camera.position.set(-2.0, 1.2, 2.2);
    cameraRef.current = camera;

    // 2. Renderer with performance-optimized pixel ratio and shadow settings
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        powerPreference: "high-performance",
        failIfMajorPerformanceCaveat: false,
      });
      renderer.setSize(width, height);
      // Cap pixel ratio to 1.5 to prevent GPU fill-rate collapse on high-DPI displays
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFShadowMap;
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      container.appendChild(renderer.domElement);
      rendererRef.current = renderer;
    } catch (err) {
      console.error("[MissionSimulation3D] WebGL initialization failed:", err);
      setWebGlAvailable(false);
      return;
    }

    // Handle WebGL context loss gracefully
    const handleContextLost = (event: Event) => {
      event.preventDefault();
      console.warn("[MissionSimulation3D] WebGL context lost - pausing render loop");
    };
    const handleContextRestored = () => {
      console.info("[MissionSimulation3D] WebGL context restored");
    };
    renderer.domElement.addEventListener("webglcontextlost", handleContextLost, false);
    renderer.domElement.addEventListener("webglcontextrestored", handleContextRestored, false);

    // 3. OrbitControls (Smooth, unrestricted zoom/pan/orbit)
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enableZoom = true;
    controls.zoomSpeed = 1.6;
    controls.enableRotate = true;
    controls.rotateSpeed = 0.8;
    controls.enablePan = true;
    controls.panSpeed = 0.8;
    controls.screenSpacePanning = true;
    controls.minDistance = 0.1;
    controls.maxDistance = 1500;
    controls.maxPolarAngle = Math.PI / 2 - 0.01;
    controls.target.set(0, 0.25, 0);
    controlsRef.current = controls;

    // Instantly yield scripted camera glide whenever the user manually touches or drags controls
    controls.addEventListener("start", () => {
      cameraTransitionRef.current = null;
    });

    // Also cancel automated transition on mouse wheel zoom
    const onWheel = () => {
      cameraTransitionRef.current = null;
    };
    renderer.domElement.addEventListener("wheel", onWheel, { passive: true });

    // 4. Lighting (Daylight outdoor SAR atmosphere with 1024x1024 shadow map)
    const hemiLight = new THREE.HemisphereLight(0xcce7e8, 0x142e26, 1.8);
    scene.add(hemiLight);

    const sunLight = new THREE.DirectionalLight(0xfff8ed, 2.5);
    sunLight.position.set(60, 140, 80);
    sunLight.castShadow = true;
    sunLight.shadow.mapSize.width = 1024;
    sunLight.shadow.mapSize.height = 1024;
    sunLight.shadow.camera.near = 10;
    sunLight.shadow.camera.far = 450;
    const shadowD = 120;
    sunLight.shadow.camera.left = -shadowD;
    sunLight.shadow.camera.right = shadowD;
    sunLight.shadow.camera.top = shadowD;
    sunLight.shadow.camera.bottom = -shadowD;
    sunLight.shadow.bias = -0.0001;
    scene.add(sunLight);

    const ambientLight = new THREE.AmbientLight(0x10352c, 1.0);
    scene.add(ambientLight);

    // 5. Continuous Natural Ground Terrain with cached organic texture
    const groundSize = 1800;
    const groundGeo = new THREE.PlaneGeometry(groundSize, groundSize);
    const terrainTex = createTerrainTexture();
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x112821,
      map: terrainTex,
      roughness: 0.94,
      metalness: 0.04,
    });
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = 0;
    ground.receiveShadow = true;
    scene.add(ground);

    // 6. Realistic Sized Launch Helipad (1.6m diameter, strictly at launch position 0, 0, 0)
    const padGroup = new THREE.Group();
    padGroup.name = "RealisticHelipad";
    padGroup.position.set(0, 0.015, 0);

    const padBase = new THREE.Mesh(
      new THREE.CylinderGeometry(0.8, 0.82, 0.03, 32),
      new THREE.MeshStandardMaterial({
        color: 0x1e293b,
        roughness: 0.7,
        metalness: 0.3,
      })
    );
    padBase.receiveShadow = true;
    padGroup.add(padBase);

    const padRing = new THREE.Mesh(
      new THREE.RingGeometry(0.72, 0.79, 32),
      new THREE.MeshBasicMaterial({ color: 0xfacc15, side: THREE.DoubleSide })
    );
    padRing.rotation.x = -Math.PI / 2;
    padRing.position.y = 0.016;
    padGroup.add(padRing);

    const padInnerRing = new THREE.Mesh(
      new THREE.RingGeometry(0.5, 0.54, 32),
      new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide })
    );
    padInnerRing.rotation.x = -Math.PI / 2;
    padInnerRing.position.y = 0.016;
    padGroup.add(padInnerRing);

    // Helipad "H" Marking
    const hMat = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
    const hBarLeft = new THREE.Mesh(new THREE.PlaneGeometry(0.065, 0.44), hMat);
    hBarLeft.rotation.x = -Math.PI / 2;
    hBarLeft.position.set(-0.15, 0.017, 0);
    padGroup.add(hBarLeft);

    const hBarRight = new THREE.Mesh(new THREE.PlaneGeometry(0.065, 0.44), hMat);
    hBarRight.rotation.x = -Math.PI / 2;
    hBarRight.position.set(0.15, 0.017, 0);
    padGroup.add(hBarRight);

    const hBarMid = new THREE.Mesh(new THREE.PlaneGeometry(0.24, 0.065), hMat);
    hBarMid.rotation.x = -Math.PI / 2;
    hBarMid.position.set(0, 0.017, 0);
    padGroup.add(hBarMid);

    // 4 Corner LED Pad Beacons
    const ledPositions = [
      [0.65, 0.65],
      [-0.65, 0.65],
      [-0.65, -0.65],
      [0.65, -0.65],
    ];
    ledPositions.forEach(([lx, lz]) => {
      const ledPost = new THREE.Mesh(
        new THREE.CylinderGeometry(0.02, 0.025, 0.06, 8),
        new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.8 })
      );
      ledPost.position.set(lx, 0.03, lz);
      padGroup.add(ledPost);

      const ledGlow = new THREE.Mesh(
        new THREE.SphereGeometry(0.035, 8, 8),
        new THREE.MeshBasicMaterial({ color: 0x54e7b1 })
      );
      ledGlow.position.set(lx, 0.065, lz);
      padGroup.add(ledGlow);
    });

    scene.add(padGroup);

    // 7. Search Area Polygon Root
    const polyGroup = new THREE.Group();
    polyGroup.name = "SearchPolygonRoot";
    scene.add(polyGroup);
    polygonGroupRef.current = polyGroup;

    // 8. Drone Root & Shadow Cue
    const droneGroup = new THREE.Group();
    droneGroup.name = "DroneMovingRoot";
    droneGroup.position.set(0, 0, 0);
    scene.add(droneGroup);
    droneGroupRef.current = droneGroup;

    // Attached Medical Kit mounted under drone belly
    const dronePayloadGroup = new THREE.Group();
    dronePayloadGroup.name = "DroneAttachedPayload";
    const mountedKit = createMedicalKitModel();
    dronePayloadGroup.position.set(0, -0.16, 0);
    dronePayloadGroup.add(mountedKit);
    droneGroup.add(dronePayloadGroup);
    dronePayloadRef.current = dronePayloadGroup;

    // Active Drop Animations Group
    const dropContainer = new THREE.Group();
    dropContainer.name = "ActiveDropAnimations";
    scene.add(dropContainer);
    dropContainerRef.current = dropContainer;

    const shadowMesh = new THREE.Mesh(
      new THREE.CircleGeometry(0.85, 24),
      new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.45 })
    );
    shadowMesh.rotation.x = -Math.PI / 2;
    shadowMesh.position.y = 0.025;
    scene.add(shadowMesh);
    groundShadowRef.current = shadowMesh;

    // 9. Camera Footprint attached to drone
    const footprint = createCameraFootprint({ hfovDeg: 68, vfovDeg: 46 });
    scene.add(footprint.group);
    footprintRef.current = footprint;

    // 10. Completed Trail Line
    const maxTrailPoints = 3000;
    const trailPositions = new Float32Array(maxTrailPoints * 3);
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(trailPositions, 3));
    trailGeo.setDrawRange(0, 0);
    const trailMat = new THREE.LineBasicMaterial({
      color: 0x00f5d4,
      transparent: true,
      opacity: 0.85,
    });
    const trailLine = new THREE.Line(trailGeo, trailMat);
    scene.add(trailLine);
    trailLineRef.current = trailLine;

    // 11. Groups for Survivors, Targets, and Selection Highlights
    const survivorsGroup = new THREE.Group();
    survivorsGroup.name = "ScenarioSurvivorsGroup";
    scene.add(survivorsGroup);
    survivorsGroupRef.current = survivorsGroup;

    const targetsGroup = new THREE.Group();
    targetsGroup.name = "AutonomousTargetsGroup";
    scene.add(targetsGroup);
    targetsGroupRef.current = targetsGroup;

    const highlightGroup = new THREE.Group();
    highlightGroup.name = "SelectionHighlightGroup";
    scene.add(highlightGroup);
    highlightGroupRef.current = highlightGroup;

    // Load actual Drone 3D model once (scaled to 1.75m medical delivery drone span)
    loadDroneModel(1.75).then((loaded) => {
      if (isDisposed) {
        loaded.dispose();
        return;
      }
      droneObjectRef.current = loaded;
      droneGroup.add(loaded.group);
      setIsCustomModel(loaded.isCustomModel);
    }).catch((err) => {
      console.warn("[MissionSimulation3D] Drone loading error:", err);
    });

    // Handle container resize
    const resizeObserver = new ResizeObserver(() => {
      if (!container || !renderer || !camera || isDisposed) return;
      const w = container.clientWidth || 800;
      const h = container.clientHeight || 540;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObserver.observe(container);

    // 12. Single Animation & Render Loop (60 FPS, with throttled 4Hz HUD state updates)
    let animationFrameId: number;
    let lastTime = performance.now();
    let frameCount = 0;
    let fpsTimer = performance.now();
    let showcaseAngle = 0;
    let lastHudUpdate = 0;

    // Reusable scratch vector to prevent heap allocations per frame
    const curDronePos = new THREE.Vector3();
    const droneDelta = new THREE.Vector3();

    const renderTick = () => {
      if (isDisposed) return;

      const currentTime = performance.now();
      const deltaSec = Math.min((currentTime - lastTime) / 1000, 0.1);
      lastTime = currentTime;

      // Track FPS at 1Hz
      frameCount++;
      if (currentTime - fpsTimer >= 1000) {
        setFps((frameCount * 1000) / (currentTime - fpsTimer));
        frameCount = 0;
        fpsTimer = currentTime;
      }

      // Telemetry Interpolation or Pre-Flight Anchor
      const buffer = telemetryBufferRef.current;
      const curOrigin = originRef.current;
      let lat = curOrigin.lat;
      let lon = curOrigin.lon;
      let yaw = 0;
      let alt = 0;
      let speed = 0;
      let seq = "-";
      let isArmed = false;
      const hasLiveTelemetry = buffer.length > 0;

      if (hasLiveTelemetry) {
        const targetTime = performance.now() - 250;
        let before: VehicleState | null = null;
        let after: VehicleState | null = null;

        for (let i = buffer.length - 1; i >= 0; i--) {
          const sample = buffer[i];
          if (sample._receive_time_ms && sample._receive_time_ms <= targetTime) {
            before = sample;
            if (i < buffer.length - 1) after = buffer[i + 1];
            break;
          }
        }

        if (before && after && before._receive_time_ms && after._receive_time_ms) {
          const dt = after._receive_time_ms - before._receive_time_ms;
          const alpha = dt > 0 ? (targetTime - before._receive_time_ms) / dt : 0;
          lat = before.latitude + (after.latitude - before.latitude) * alpha;
          lon = before.longitude + (after.longitude - before.longitude) * alpha;
          let dyaw = after.yaw_deg - before.yaw_deg;
          if (dyaw > 180) dyaw -= 360;
          if (dyaw < -180) dyaw -= 360;
          yaw = before.yaw_deg + dyaw * alpha;
          alt = before.relative_altitude_m + (after.relative_altitude_m - before.relative_altitude_m) * alpha;
          seq = before.sequence.toString();
          speed = before.ground_speed_m_s;
          isArmed = before.armed;
        } else if (before) {
          lat = before.latitude; lon = before.longitude; yaw = before.yaw_deg;
          alt = before.relative_altitude_m; seq = before.sequence.toString();
          speed = before.ground_speed_m_s; isArmed = before.armed;
        } else if (buffer.length > 0) {
          const latest = buffer[buffer.length - 1];
          lat = latest.latitude; lon = latest.longitude; yaw = latest.yaw_deg;
          alt = latest.relative_altitude_m; seq = latest.sequence.toString();
          speed = latest.ground_speed_m_s; isArmed = latest.armed;
        }
      }

      // Convert geo to local 3D scene coordinates without heap allocations
      let sceneX = 0, sceneZ = 0;
      const sceneY = Math.max(0, alt);

      if (hasLiveTelemetry && (lat !== 0 || lon !== 0)) {
        const pt = geoToScene(lat, lon, curOrigin.lat, curOrigin.lon);
        sceneX = pt.x;
        sceneZ = pt.z;
      } else {
        sceneX = 0;
        sceneZ = 0;
      }

      curDronePos.set(sceneX, sceneY, sceneZ);

      // Directly position 3D Drone & Heading in WebGL layer
      if (droneGroupRef.current) {
        droneGroupRef.current.position.copy(curDronePos);
        droneGroupRef.current.rotation.y = -yaw * (Math.PI / 180);
      }

      // Position ground contact shadow cue
      if (groundShadowRef.current) {
        groundShadowRef.current.position.set(sceneX, 0.025, sceneZ);
        const shadowScale = Math.max(0.4, 1.0 - Math.min(sceneY / 35, 0.6));
        groundShadowRef.current.scale.set(shadowScale, shadowScale, shadowScale);
      }

      // --- Autonomous Medical Kit Drop & Delivery Controller ---
      const activeTargets = Object.values(useGcsStore.getState().targets || {});
      const completedDrops = completedDropsRef.current;
      const activeDrops = activeDropsRef.current;
      const dropContainer = dropContainerRef.current;

      if (dropContainer) {
        activeTargets.forEach((t) => {
          if (!t.latitude || !t.longitude) return;
          if (completedDrops.has(t.target_id) || activeDrops.has(t.target_id)) return;

          let delivSceneX = 0;
          let delivSceneZ = 0;
          if (t.delivery_point_east_m !== undefined && t.delivery_point_east_m !== null &&
              t.delivery_point_north_m !== undefined && t.delivery_point_north_m !== null) {
            delivSceneX = t.delivery_point_east_m;
            delivSceneZ = -t.delivery_point_north_m;
          } else {
            const pt = geoToScene(t.latitude, t.longitude, curOrigin.lat, curOrigin.lon);
            delivSceneX = pt.x + 2.0;
            delivSceneZ = pt.z;
          }

          const distToStandoff = Math.hypot(curDronePos.x - delivSceneX, curDronePos.z - delivSceneZ);
          const isDeliveryState = [
            "RELEASED", "DELIVERED", "DELIVERED_VERIFIED"
          ].includes(t.delivery_state);
          const isApproachingClose = t.delivery_state === "APPROACHING" && distToStandoff < 5.0 && curDronePos.y < 16.0;

          if (isDeliveryState || isApproachingClose) {
            if (distToStandoff < 35.0 && curDronePos.y > 0.8) {
              // Spawn dropping payload with parachute!
              const dropGroup = new THREE.Group();
              dropGroup.name = `DroppingPayload_${t.target_id}`;

              const fallingKit = createMedicalKitModel();
              const fallingChute = createParachuteModel();
              dropGroup.add(fallingKit);
              dropGroup.add(fallingChute);

              dropGroup.position.copy(curDronePos);
              dropGroup.position.y -= 0.15;
              dropContainer.add(dropGroup);

              const durationMs = Math.max(2200, Math.min(4200, curDronePos.y * 110));

              activeDrops.set(t.target_id, {
                targetId: t.target_id,
                group: dropGroup,
                startPos: curDronePos.clone(),
                endPos: new THREE.Vector3(delivSceneX, 0, delivSceneZ),
                startTime: currentTime,
                durationMs,
              });

              if (dronePayloadRef.current) {
                dronePayloadRef.current.visible = false;
              }
              setTargetDropTick((c) => c + 1);
            } else {
              completedDrops.add(t.target_id);
            }
          }
        });
      }

      // Progress active drops
      if (activeDrops.size > 0 && dropContainer) {
        const finishedIds: string[] = [];
        activeDrops.forEach((drop, tid) => {
          const u = Math.min(1.0, (currentTime - drop.startTime) / drop.durationMs);
          const easeY = Math.pow(u, 1.4);
          const x = THREE.MathUtils.lerp(drop.startPos.x, drop.endPos.x, u);
          const z = THREE.MathUtils.lerp(drop.startPos.z, drop.endPos.z, u);
          const y = THREE.MathUtils.lerp(drop.startPos.y, drop.endPos.y, easeY);

          drop.group.position.set(x, y, z);

          // Aerodynamic pendulum sway under parachute
          const sway = (1 - u) * 0.14;
          drop.group.rotation.z = Math.sin(u * Math.PI * 6) * sway;
          drop.group.rotation.x = Math.cos(u * Math.PI * 5) * (sway * 0.7);

          if (u >= 1.0) {
            finishedIds.push(tid);
            dropContainer.remove(drop.group);
            completedDrops.add(tid);

            // Ground touchdown wave ring
            const impactGeo = new THREE.RingGeometry(0.1, 0.45, 24);
            const impactMat = new THREE.MeshBasicMaterial({ color: 0x10b981, side: THREE.DoubleSide, transparent: true, opacity: 0.9 });
            const impactRing = new THREE.Mesh(impactGeo, impactMat);
            impactRing.rotation.x = -Math.PI / 2;
            impactRing.position.set(drop.endPos.x, 0.05, drop.endPos.z);
            scene.add(impactRing);

            let ringU = 0;
            const animateRing = () => {
              ringU += 0.04;
              impactRing.scale.set(1 + ringU * 4, 1 + ringU * 4, 1);
              impactMat.opacity = Math.max(0, 0.9 * (1 - ringU));
              if (ringU < 1.0) {
                requestAnimationFrame(animateRing);
              } else {
                scene.remove(impactRing);
                impactGeo.dispose();
                impactMat.dispose();
              }
            };
            requestAnimationFrame(animateRing);
          }
        });

        if (finishedIds.length > 0) {
          finishedIds.forEach((id) => activeDrops.delete(id));
          setTargetDropTick((c) => c + 1);
        }
      }

      // Drone payload visibility between drops
      if (dronePayloadRef.current && activeDrops.size === 0) {
        const totalScenarioSurvivors = scenarioSurvivors.length || 3;
        const totalDelivered = completedDrops.size;
        const storeInventory = useGcsStore.getState().payloadRemaining;
        const hasKits =
          storeInventory !== null && storeInventory !== undefined
            ? storeInventory > 0
            : totalDelivered < totalScenarioSurvivors;
        dronePayloadRef.current.visible = hasKits;
      }

      // Update camera footprint attached to drone
      if (footprintRef.current) {
        footprintRef.current.update(
          curDronePos,
          yaw,
          0,
          0,
          sceneY
        );
      }

      // Update propeller spin
      if (droneObjectRef.current) {
        droneObjectRef.current.updateRotors(deltaSec, isArmed || sceneY > 0.1);
      }

      // CRITICAL FIX: Throttle React HUD state updates to 4Hz (every 250ms) instead of 60Hz
      // Eliminates React reconciliation starvation, preventing browser freeze and input lockup
      if (currentTime - lastHudUpdate >= 250) {
        lastHudUpdate = currentTime;
        setReadout2D({ lat, lon, yaw, alt, speed, seq });
        const rev = sceneToGeo(sceneX, sceneZ, curOrigin.lat, curOrigin.lon);
        setReadout3D({
          lat: rev.lat,
          lon: rev.lon,
          yaw,
          alt: sceneY,
          sceneX,
          sceneY,
          sceneZ,
        });
      }

      // Camera Animation Transition Handling
      const transition = cameraTransitionRef.current;
      if (transition) {
        const elapsed = currentTime - transition.startTime;
        const progress = Math.min(1, elapsed / transition.durationMs);
        const ease = progress < 0.5 ? 4 * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 3) / 2;

        camera.position.lerpVectors(transition.startPos, transition.targetPos, ease);
        controls.target.lerpVectors(transition.startLookAt, transition.targetLookAt, ease);

        if (progress >= 1) {
          cameraTransitionRef.current = null;
        }
      } else {
        // In FOLLOW mode during flight, shift camera and target by vehicle delta without resetting user's zoom or orbit
        droneDelta.subVectors(curDronePos, prevDronePosRef.current);
        if (cameraModeRef.current === "FOLLOW" && droneDelta.lengthSq() > 1e-6) {
          controls.target.add(droneDelta);
          camera.position.add(droneDelta);
        }

        // Showcase cinematic presentation orbit
        if (showcaseRef.current) {
          showcaseAngle += deltaSec * 0.35;
          const radius = sceneY > 0.5 ? 6.5 : 3.2;
          const camY = sceneY > 0.5 ? Math.max(2.5, sceneY + 2.0) : 1.4;
          camera.position.x = sceneX + Math.sin(showcaseAngle) * radius;
          camera.position.z = sceneZ + Math.cos(showcaseAngle) * radius;
          camera.position.y = camY;
          controls.target.set(sceneX, sceneY > 0.5 ? sceneY : 0.25, sceneZ);
        }
      }

      prevDronePosRef.current.copy(curDronePos);
      controls.update();
      renderer.render(scene, camera);
      animationFrameId = requestAnimationFrame(renderTick);
    };

    animationFrameId = requestAnimationFrame(renderTick);

    // Canvas click raycaster for selecting 3D survivors directly
    const handleCanvasClick = (e: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1
      );

      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(mouse, camera);

      if (survivorsGroupRef.current) {
        const hits = raycaster.intersectObjects(survivorsGroupRef.current.children, true);
        if (hits.length > 0) {
          let curr: THREE.Object3D | null = hits[0].object;
          while (curr && curr.parent && curr.parent !== survivorsGroupRef.current) {
            curr = curr.parent;
          }
          if (curr && (curr as any).userData?.survivorId) {
            handleSelectSurvivorRef.current((curr as any).userData.survivorId);
          }
        }
      }
    };

    renderer.domElement.addEventListener("click", handleCanvasClick);

    // Clean unmount: cancel RAF loop, remove listeners, dispose WebGL resources
    return () => {
      isDisposed = true;
      cancelAnimationFrame(animationFrameId);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("click", handleCanvasClick);
      renderer.domElement.removeEventListener("wheel", onWheel);
      renderer.domElement.removeEventListener("webglcontextlost", handleContextLost);
      renderer.domElement.removeEventListener("webglcontextrestored", handleContextRestored);
      controls.dispose();
      renderer.dispose();
      if (droneObjectRef.current) droneObjectRef.current.dispose();
      if (footprintRef.current) footprintRef.current.dispose();
      if (renderer.domElement.parentElement) {
        renderer.domElement.parentElement.removeChild(renderer.domElement);
      }
    };
  }, []); // Strictly empty dependency array: exactly ONE active loop per mount

  // Mode Selection Camera Triggers
  const setModeAndAnimateCamera = useCallback((mode: CameraMode) => {
    setCameraMode(mode);
    setShowcaseActive(false);

    const dronePos = prevDronePosRef.current;

    if (mode === "FOLLOW") {
      const isFlying = dronePos.y > 0.5;
      if (isFlying) {
        // Tight, clear follow view keeping the 0.85m aircraft readable and prominent
        // without distorting real metric scale of surrounding terrain and people
        const followDist = 4.5;
        const followHeight = 2.4;
        const yawRad = -readout2D.yaw * (Math.PI / 180);
        const targetPos = new THREE.Vector3(
          dronePos.x - Math.sin(yawRad) * followDist,
          dronePos.y + followHeight,
          dronePos.z - Math.cos(yawRad) * followDist
        );
        startCameraTransition(targetPos, dronePos, 800);
      } else {
        // Close inspection view of drone on launch pad
        const targetPos = new THREE.Vector3(dronePos.x - 2.0, dronePos.y + 1.2, dronePos.z + 2.2);
        startCameraTransition(targetPos, new THREE.Vector3(dronePos.x, dronePos.y + 0.25, dronePos.z), 700);
      }
    } else if (mode === "TOP_DOWN") {
      // Useful inspection distance looking directly down at current subject
      if (selectedSurvivorId) {
        const foundScenario = scenarioSurvivors.find((s: any) => s.id === selectedSurvivorId);
        const lat = foundScenario?.lat ?? foundScenario?.latitude ?? targets[selectedSurvivorId]?.latitude;
        const lon = foundScenario?.lng ?? foundScenario?.lon ?? foundScenario?.longitude ?? targets[selectedSurvivorId]?.longitude;
        if (lat !== undefined && lon !== undefined) {
          const pt = geoToScene(lat, lon, originRef.current.lat, originRef.current.lon);
          startCameraTransition(
            new THREE.Vector3(pt.x, 8, pt.z + 0.01),
            new THREE.Vector3(pt.x, 0, pt.z),
            750
          );
        }
      } else {
        // Look directly down at drone from inspection altitude (12m or drone altitude + 7m)
        const topHeight = Math.max(12, dronePos.y + 7);
        startCameraTransition(
          new THREE.Vector3(dronePos.x, topHeight, dronePos.z + 0.01),
          new THREE.Vector3(dronePos.x, dronePos.y, dronePos.z),
          750
        );
      }
    } else if (mode === "OVERVIEW") {
      // Frame complete search area polygon
      const { center, distance } = overviewBoundsRef.current;
      const targetPos = new THREE.Vector3(
        center.x,
        distance * 0.95,
        center.z + distance * 0.8
      );
      startCameraTransition(targetPos, center, 900);
    } else if (mode === "ORBIT") {
      if (selectedSurvivorId) {
        const foundScenario = scenarioSurvivors.find((s: any) => s.id === selectedSurvivorId);
        const lat = foundScenario?.lat ?? foundScenario?.latitude ?? targets[selectedSurvivorId]?.latitude;
        const lon = foundScenario?.lng ?? foundScenario?.lon ?? foundScenario?.longitude ?? targets[selectedSurvivorId]?.longitude;
        if (lat !== undefined && lon !== undefined) {
          const pt = geoToScene(lat, lon, originRef.current.lat, originRef.current.lon);
          const isLying = foundScenario?.bodyState === "lying";
          const targetY = isLying ? 0.3 : 0.85;
          startCameraTransition(
            new THREE.Vector3(pt.x + 2.2, targetY + 1.2, pt.z + 2.2),
            new THREE.Vector3(pt.x, targetY, pt.z),
            750
          );
        }
      } else {
        // Close orbit inspection around drone
        const targetPos = new THREE.Vector3(dronePos.x - 2.2, dronePos.y + 1.4, dronePos.z + 2.5);
        const targetLookAt = new THREE.Vector3(dronePos.x, dronePos.y + 0.25, dronePos.z);
        startCameraTransition(targetPos, targetLookAt, 750);
      }
    }
  }, [readout2D.yaw, selectedSurvivorId, scenarioSurvivors, targets, startCameraTransition]);

  // Flexible Precision Zoom Handlers
  const handleZoomStep = useCallback((factor: number) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;

    cameraTransitionRef.current = null;
    const offset = new THREE.Vector3().subVectors(camera.position, controls.target);
    const newDistance = offset.length() * factor;
    const clampedDistance = THREE.MathUtils.clamp(newDistance, controls.minDistance, controls.maxDistance);

    offset.setLength(clampedDistance);
    camera.position.copy(controls.target).add(offset);
    controls.update();
  }, []);

  const handleResetZoom = useCallback(() => {
    const dronePos = prevDronePosRef.current;
    const targetPos = new THREE.Vector3(dronePos.x - 2.0, dronePos.y + 1.2, dronePos.z + 2.2);
    const targetLookAt = new THREE.Vector3(dronePos.x, dronePos.y + 0.25, dronePos.z);
    startCameraTransition(targetPos, targetLookAt, 650);
  }, [startCameraTransition]);

  // Keyboard shortcuts (+ / - / r) for flexible zooming
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "+" || e.key === "=") {
        handleZoomStep(0.7);
      } else if (e.key === "-" || e.key === "_") {
        handleZoomStep(1.35);
      } else if (e.key.toLowerCase() === "r") {
        handleResetZoom();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleZoomStep, handleResetZoom]);

  // Render & Update Search Area Polygon (Runs strictly when polygon vertices actually change)
  useEffect(() => {
    const polyGroup = polygonGroupRef.current;
    if (!polyGroup) return;

    while (polyGroup.children.length > 0) {
      const child = polyGroup.children[0];
      polyGroup.remove(child);
      if ((child as any).geometry) (child as any).geometry.dispose();
      if ((child as any).material) {
        if (Array.isArray((child as any).material)) {
          (child as any).material.forEach((m: any) => m.dispose());
        } else {
          (child as any).material.dispose();
        }
      }
    }

    if (polyScenePoints.length < 3) return;

    // 1. Raised Physical Search Surface Mesh (y = 0.03m)
    const shapeCoords = polyScenePoints.map((p) => new THREE.Vector2(p.x, p.z));
    const triangles = THREE.ShapeUtils.triangulateShape(shapeCoords, []);

    const polyGeo = new THREE.BufferGeometry();
    const positions: number[] = [];
    const normals: number[] = [];

    triangles.forEach((tri) => {
      tri.forEach((idx) => {
        const pt = polyScenePoints[idx];
        positions.push(pt.x, 0.03, pt.z);
        normals.push(0, 1, 0);
      });
    });

    polyGeo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    polyGeo.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));

    const polySurfaceMat = new THREE.MeshStandardMaterial({
      color: 0x194537,
      roughness: 0.86,
      metalness: 0.06,
      side: THREE.DoubleSide,
    });
    const polyMesh = new THREE.Mesh(polyGeo, polySurfaceMat);
    polyMesh.receiveShadow = true;
    polyGroup.add(polyMesh);

    // 2. Clipped Metric Search Grid (Pre-calculated via useMemo)
    if (clippedGridSegments.length > 0) {
      const gridGeo = new THREE.BufferGeometry().setFromPoints(clippedGridSegments);
      const gridMat = new THREE.LineBasicMaterial({
        color: 0x34d399,
        transparent: true,
        opacity: 0.4,
      });
      const gridLineSegments = new THREE.LineSegments(gridGeo, gridMat);
      polyGroup.add(gridLineSegments);
    }

    // 3. Perimeter Boundary Line
    const loopPoints = polyScenePoints.map((p) => new THREE.Vector3(p.x, 0.045, p.z));
    loopPoints.push(loopPoints[0]);
    const perimeterGeo = new THREE.BufferGeometry().setFromPoints(loopPoints);
    const perimeterMat = new THREE.LineBasicMaterial({
      color: 0x54e7b1,
      linewidth: 2,
    });
    polyGroup.add(new THREE.Line(perimeterGeo, perimeterMat));

    // 4. Boundary Stakes at Corner Vertices
    polyScenePoints.forEach((v, idx) => {
      const post = new THREE.Mesh(
        new THREE.CylinderGeometry(0.08, 0.1, 1.8, 12),
        new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.4, metalness: 0.8 })
      );
      post.position.set(v.x, 0.9, v.z);
      post.castShadow = true;
      polyGroup.add(post);

      const beaconColor = idx === 0 ? 0xfacc15 : 0x34d399;
      const beacon = new THREE.Mesh(
        new THREE.SphereGeometry(0.14, 12, 12),
        new THREE.MeshBasicMaterial({ color: beaconColor })
      );
      beacon.position.set(v.x, 1.85, v.z);
      polyGroup.add(beacon);

      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.35, 0.44, 24),
        new THREE.MeshBasicMaterial({ color: beaconColor, side: THREE.DoubleSide })
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.048;
      polyGroup.add(ring);
    });
  }, [polyKey, clippedGridSegments]);

  // Update Flight Trail
  useEffect(() => {
    if (!trailLineRef.current || !model.trail) return;
    const curOrigin = originRef.current;
    const trailLine = trailLineRef.current;
    const geo = trailLine.geometry as THREE.BufferGeometry;
    const posAttr = geo.attributes.position as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;

    const count = Math.min(model.trail.length, 3000);
    let idx = 0;
    for (let i = 0; i < count; i++) {
      const pt = model.trail[i];
      const s = geoToScene(pt.lat, pt.lon, curOrigin.lat, curOrigin.lon);
      arr[idx++] = s.x;
      arr[idx++] = 0.06;
      arr[idx++] = s.z;
    }
    posAttr.needsUpdate = true;
    geo.setDrawRange(0, count);
  }, [model.trail]);

  // Render Scenario Ground Truth Survivors (Runs strictly when scenario survivors data changes)
  useEffect(() => {
    const group = survivorsGroupRef.current;
    if (!group) return;

    while (group.children.length > 0) {
      const child = group.children[0];
      group.remove(child);
      if ((child as any).dispose) (child as any).dispose();
    }

    const curOrigin = originRef.current;

    scenarioSurvivors.forEach((s: any) => {
      const lat = s.lat ?? s.latitude;
      const lon = s.lng ?? s.lon ?? s.longitude;
      if (lat === undefined || lon === undefined) return;

      const pt = geoToScene(lat, lon, curOrigin.lat, curOrigin.lon);
      // All survivors must be in the standing posture
      const bodyState: "standing" | "lying" = "standing";

      loadHumanModel(bodyState, s.id).then((human) => {
        if (!group) return;
        const root = new THREE.Group();
        root.name = `ScenarioTruth_${s.id}`;
        (root as any).userData = { survivorId: s.id, type: "TRUTH" };
        root.position.set(pt.x, 0, pt.z);
        root.add(human.group);

        // Amber Ground Truth Ring
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(0.85, 1.15, 32),
          new THREE.MeshBasicMaterial({ color: 0xf59e0b, side: THREE.DoubleSide })
        );
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = 0.04;
        root.add(ring);

        // Amber Floating Location Beacon
        const beaconGeo = new THREE.ConeGeometry(0.24, 0.75, 8);
        const beaconMat = new THREE.MeshBasicMaterial({ color: 0xf59e0b });
        const beacon = new THREE.Mesh(beaconGeo, beaconMat);
        beacon.rotation.x = Math.PI;
        beacon.position.y = 2.4;
        root.add(beacon);

        // Billboard Text Label
        const labelText = `[TRUTH] ${s.id} (${bodyState.toUpperCase()})`;
        const labelSprite = createFloatingLabel(labelText, "#f59e0b");
        labelSprite.position.y = 2.9;
        root.add(labelSprite);

        group.add(root);
      });
    });
  }, [scenarioSurvivorsKey]);

  // Render Autonomous Predicted Targets (Runs strictly when target predictions change)
  useEffect(() => {
    const group = targetsGroupRef.current;
    if (!group) return;

    while (group.children.length > 0) {
      const child = group.children[0];
      group.remove(child);
      if ((child as any).dispose) (child as any).dispose();
    }

    const curOrigin = originRef.current;
    const targetList: Target[] = Object.values(targets || {}).filter(
      (t) => t.state !== "REJECTED" && t.state !== "CANDIDATE"
    );

    targetList.forEach((t) => {
      if (t.latitude === undefined || t.longitude === undefined) return;
      const pt = geoToScene(t.latitude, t.longitude, curOrigin.lat, curOrigin.lon);

      const root = new THREE.Group();
      root.name = `AutonomousPrediction_${t.target_id}`;
      (root as any).userData = { survivorId: t.target_id, type: "PREDICTION" };
      root.position.set(pt.x, 0, pt.z);

      // Delivery state and billboard badge
      const confPct = t.detection_confidence ? `${Math.round(t.detection_confidence * 100)}%` : "";
      let deliveryTag = "";
      let markerColor = 0x06b6d4;
      let labelColor = "#06b6d4";

      if (t.delivery_state === "DELIVERED_VERIFIED" || t.delivery_state === "DELIVERED") {
        deliveryTag = " · DELIVERED ✓";
        markerColor = 0x10b981;
        labelColor = "#10b981";
      } else if (t.delivery_state === "APPROACHING") {
        deliveryTag = " · APPROACHING";
        markerColor = 0xf59e0b;
        labelColor = "#f59e0b";
      } else if (t.delivery_state === "DELIVERY_QUEUED") {
        deliveryTag = " · QUEUED";
        markerColor = 0x38bdf8;
        labelColor = "#38bdf8";
      } else if (t.delivery_state === "RELEASED") {
        deliveryTag = " · RELEASED";
        markerColor = 0xf97316;
        labelColor = "#f97316";
      } else if (t.delivery_state === "FAILED") {
        deliveryTag = " · FAILED";
        markerColor = 0xef4444;
        labelColor = "#ef4444";
      } else if (t.delivery_state === "DEFERRED") {
        deliveryTag = " · DEFERRED";
        markerColor = 0xf97316;
        labelColor = "#f97316";
      }

      // Detection Ring
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(1.2, 1.45, 32),
        new THREE.MeshBasicMaterial({ color: markerColor, side: THREE.DoubleSide })
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.045;
      root.add(ring);

      // Diamond Beacon
      const beaconGeo = new THREE.OctahedronGeometry(0.26, 0);
      const beaconMat = new THREE.MeshBasicMaterial({ color: markerColor });
      const beacon = new THREE.Mesh(beaconGeo, beaconMat);
      beacon.position.y = 2.8;
      root.add(beacon);

      // Billboard Label
      const labelText = `[PREDICTION] ${t.target_id.slice(0, 8)} ${confPct}${deliveryTag}`;
      const labelSprite = createFloatingLabel(labelText, labelColor);
      labelSprite.position.y = 3.4;
      root.add(labelSprite);

      // Standoff Safe Delivery Point Marker & Kit
      let delivSceneX = pt.x + 2.0;
      let delivSceneZ = pt.z;
      let hasCustomStandoff = false;
      if (t.delivery_point_east_m !== undefined && t.delivery_point_east_m !== null &&
          t.delivery_point_north_m !== undefined && t.delivery_point_north_m !== null) {
        delivSceneX = t.delivery_point_east_m;
        delivSceneZ = -t.delivery_point_north_m;
        hasCustomStandoff = true;
      }

      const isDeliveryActiveOrDone = [
        "DELIVERY_QUEUED", "APPROACHING", "RELEASED", "DELIVERED", "DELIVERED_VERIFIED"
      ].includes(t.delivery_state);

      if (hasCustomStandoff || isDeliveryActiveOrDone) {
        const delivGroup = new THREE.Group();
        delivGroup.name = `DeliveryStandoff_${t.target_id}`;
        delivGroup.position.set(delivSceneX, 0, delivSceneZ);

        // Standoff ring on ground
        const ringCol = (t.delivery_state === "DELIVERED_VERIFIED" || t.delivery_state === "DELIVERED") ? 0x10b981 : 0xf59e0b;
        const standoffRing = new THREE.Mesh(
          new THREE.RingGeometry(0.4, 0.55, 24),
          new THREE.MeshBasicMaterial({ color: ringCol, side: THREE.DoubleSide })
        );
        standoffRing.rotation.x = -Math.PI / 2;
        standoffRing.position.y = 0.048;
        delivGroup.add(standoffRing);

        // If delivered or released, render the 3D medical kit resting at standoff point
        const isCurrentlyFalling = activeDropsRef.current.has(t.target_id);
        const hasCompletedDrop = completedDropsRef.current.has(t.target_id);

        if (isCurrentlyFalling) {
          const fallingLabel = createFloatingLabel("PAYLOAD DESCENDING...", "#f97316");
          fallingLabel.position.y = 0.7;
          delivGroup.add(fallingLabel);
        } else if (hasCompletedDrop || t.delivery_state === "DELIVERED_VERIFIED" || t.delivery_state === "RELEASED" || t.delivery_state === "DELIVERED") {
          const kitMesh = createMedicalKitModel();
          delivGroup.add(kitMesh);

          const kitLabel = createFloatingLabel("MED-KIT DELIVERED ✓", "#10b981");
          kitLabel.position.y = 0.7;
          delivGroup.add(kitLabel);
        } else if (t.delivery_state === "APPROACHING") {
          const apprLabel = createFloatingLabel("SAFE DROP STANDOFF", "#f59e0b");
          apprLabel.position.y = 0.7;
          delivGroup.add(apprLabel);
        }

        group.add(delivGroup);
      }

      group.add(root);
    });
  }, [targetsKey, targetDropTick]);

  // Render Selection Highlight on currently selected survivor
  useEffect(() => {
    const group = highlightGroupRef.current;
    if (!group) return;

    while (group.children.length > 0) {
      group.remove(group.children[0]);
    }

    if (!selectedSurvivorId) return;

    const curOrigin = originRef.current;
    let foundLat: number | undefined;
    let foundLon: number | undefined;

    const s = scenarioSurvivors.find((item: any) => item.id === selectedSurvivorId);
    if (s) {
      foundLat = s.lat ?? s.latitude;
      foundLon = s.lng ?? s.lon ?? s.longitude;
    } else {
      const t = targets[selectedSurvivorId];
      if (t) {
        foundLat = t.latitude;
        foundLon = t.longitude;
      }
    }

    if (foundLat !== undefined && foundLon !== undefined) {
      const pt = geoToScene(foundLat, foundLon, curOrigin.lat, curOrigin.lon);

      // Golden vertical highlight beam
      const beamGeo = new THREE.CylinderGeometry(1.2, 1.2, 8, 24, 1, true);
      const beamMat = new THREE.MeshBasicMaterial({
        color: 0xf59e0b,
        transparent: true,
        opacity: 0.22,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const beam = new THREE.Mesh(beamGeo, beamMat);
      beam.position.set(pt.x, 4, pt.z);
      group.add(beam);

      // Golden ground highlight ring
      const ringGeo = new THREE.RingGeometry(1.3, 1.6, 32);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0xfbbf24,
        side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(pt.x, 0.05, pt.z);
      group.add(ring);
    }
  }, [selectedSurvivorId, scenarioSurvivorsKey, targetsKey]);

  const isAwaitingTelemetry = telemetryBufferRef.current.length === 0;

  if (!webGlAvailable) {
    return (
      <div className="mission-simulation-3d-root webgl-fallback">
        <div className="webgl-error-box">
          <h3>WebGL Unavailable</h3>
          <p>Hardware accelerated 3D graphics could not be initialized in this browser session.</p>
          <p>Please switch to the <strong>2D MAP</strong> view above to monitor mission telemetry.</p>
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="mission-simulation-3d-root"
      className="mission-simulation-3d-root"
    >
      <div ref={canvasContainerRef} className="mission-3d-canvas-container" />

      {/* Top Floating Controls Bar */}
      <div className="mission-3d-toolbar">
        <div className="mission-3d-btn-group">
          {(["FOLLOW", "ORBIT", "TOP_DOWN", "OVERVIEW"] as CameraMode[]).map((mode) => (
            <button
              key={mode}
              className={`mission-3d-btn ${cameraMode === mode && !showcaseActive ? "active" : ""}`}
              onClick={() => setModeAndAnimateCamera(mode)}
              title={`Switch to ${mode.replace("_", " ")} camera`}
            >
              {mode === "FOLLOW" && "🎯"}
              {mode === "ORBIT" && "🔄"}
              {mode === "TOP_DOWN" && "⬇️"}
              {mode === "OVERVIEW" && "🌐"}
              {" " + mode.replace("_", " ")}
            </button>
          ))}
          <button
            className={`mission-3d-btn showcase-btn ${showcaseActive ? "active" : ""}`}
            onClick={() => setShowcaseActive(!showcaseActive)}
            title="Toggle cinematic showcase orbit"
          >
            ✨ SHOWCASE
          </button>
        </div>

        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span className="footprint-approx-tag">
            📷 CAM FOOTPRINT: SIMULATED / APPROXIMATE
          </span>
          <div className="mission-3d-btn-group">
            <button
              className={`mission-3d-btn ${showDiagnostics ? "active" : ""}`}
              onClick={() => setShowDiagnostics(!showDiagnostics)}
            >
              📊 TELEMETRY
            </button>
            <button
              className={`mission-3d-btn ${showGtPanel ? "active" : ""}`}
              onClick={() => setShowGtPanel(!showGtPanel)}
            >
              🧑 SURVIVORS
            </button>
          </div>
        </div>
      </div>

      {/* Honest Status Banners */}
      {isAwaitingTelemetry && (
        <div className="preview-mode-banner">
          🚀 3D ENVIRONMENT READY — AWAITING LIVE FLIGHT TELEMETRY (DRONE ON LAUNCH PAD)
        </div>
      )}
      {!isAwaitingTelemetry && model.telemetryState === "STALE" && (
        <div className="stale-telemetry-banner">
          ⚠️ STALE TELEMETRY — FREEZING DRONE AT LAST KNOWN COORDINATES
        </div>
      )}
      {!isAwaitingTelemetry && model.telemetryState === "ENDED" && (
        <div className="stale-telemetry-banner" style={{ background: "rgba(16, 185, 129, 0.9)" }}>
          🏁 MISSION COMPLETED — TELEMETRY SESSION FINALIZED
        </div>
      )}

      {/* Precision Floating Zoom Controls Widget */}
      <div className="mission-3d-zoom-controls" aria-label="Camera Zoom Controls">
        <button
          className="mission-3d-zoom-btn"
          onClick={() => handleZoomStep(0.7)}
          title="Zoom In (+ key)"
          aria-label="Zoom In"
        >
          ＋
        </button>
        <button
          className="mission-3d-zoom-btn"
          onClick={() => handleZoomStep(1.35)}
          title="Zoom Out (- key)"
          aria-label="Zoom Out"
        >
          －
        </button>
        <button
          className="mission-3d-zoom-btn zoom-reset-btn"
          onClick={handleResetZoom}
          title="Reset Zoom to Drone (R key)"
          aria-label="Reset Zoom"
        >
          ⟲
        </button>
      </div>

      {/* Real-time 2D vs 3D Diagnostic Readout */}
      {showDiagnostics && (
        <DiagnosticTelemetryReadout
          telemetry2D={readout2D}
          drone3D={readout3D}
          liveVehicle={model.liveVehicle}
          telemetryState={isAwaitingTelemetry ? "PREVIEW" as any : model.telemetryState}
          isCustomModel={isCustomModel}
          fps={fps}
          onClose={() => setShowDiagnostics(false)}
        />
      )}

      {/* Ground Truth vs Predicted Location Panel */}
      {showGtPanel && (
        <GroundTruthVsPredictedPanel
          scenarioSurvivors={scenarioSurvivors}
          targets={targets}
          selectedId={selectedSurvivorId}
          onSelectSurvivor={handleSelectSurvivor}
          onClose={() => setShowGtPanel(false)}
        />
      )}
    </div>
  );
}
