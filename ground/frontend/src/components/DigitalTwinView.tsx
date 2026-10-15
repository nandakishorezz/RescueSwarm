import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { useGcsStore } from "../stores/gcsStore";
import { cameraFrustum, cameraGroundFootprint, cameraRayToWorld, cameraWorldPose } from "../spatial/camera";
import { bodyQuaternionToThreeRotation, enuToThree, type Matrix3, type Vec3 } from "../spatial/frames";
import { interpolateReplaySample } from "../spatial/replay";

export interface RenderMetrics { fps: number; frameTimeMs: number; frameTimeP50Ms: number; frameTimeP95Ms: number; objects: number; memoryMb: number | null; }

function percentile(values: number[], fraction: number): number {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.min(ordered.length - 1, Math.floor((ordered.length - 1) * fraction))];
}

function matrix4(rotation: Matrix3): THREE.Matrix4 {
  return new THREE.Matrix4().set(
    rotation[0][0], rotation[0][1], rotation[0][2], 0,
    rotation[1][0], rotation[1][1], rotation[1][2], 0,
    rotation[2][0], rotation[2][1], rotation[2][2], 0,
    0, 0, 0, 1,
  );
}

function aircraftModel(displayScale: number, selected: boolean): THREE.Group {
  const group = new THREE.Group();
  const material = new THREE.MeshStandardMaterial({ color: selected ? 0x54e7b1 : 0xf8c15c, roughness: .55, metalness: .15 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x18302c, roughness: .8 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(.34 * displayScale, .12 * displayScale, .2 * displayScale), material);
  group.add(body);
  const nose = new THREE.Mesh(new THREE.ConeGeometry(.10 * displayScale, .25 * displayScale, 4), new THREE.MeshStandardMaterial({ color: 0xffffff }));
  nose.rotation.z = -Math.PI / 2; nose.position.x = .27 * displayScale; group.add(nose);
  for (const [x, z] of [[.22, .22], [.22, -.22], [-.22, .22], [-.22, -.22]]) {
    const arm = new THREE.Mesh(new THREE.CylinderGeometry(.018 * displayScale, .018 * displayScale, .55 * displayScale, 8), dark);
    arm.rotation.z = Math.PI / 2; arm.rotation.y = x * z > 0 ? Math.PI / 4 : -Math.PI / 4; group.add(arm);
    const rotor = new THREE.Mesh(new THREE.CylinderGeometry(.13 * displayScale, .13 * displayScale, .008 * displayScale, 24), material);
    rotor.position.set(x * displayScale, .06 * displayScale, z * displayScale); group.add(rotor);
  }
  return group;
}

function line(points: Vec3[], color: number, dashed = false): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(...enuToThree(point))));
  const material = dashed ? new THREE.LineDashedMaterial({ color, dashSize: .6, gapSize: .35 }) : new THREE.LineBasicMaterial({ color });
  const value = new THREE.Line(geometry, material); if (dashed) value.computeLineDistances(); return value;
}

function disposeTree(root: THREE.Object3D): void {
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    materials.forEach((material) => material.dispose());
  });
}

export function DigitalTwinView({ replayTimeMs, onMetrics }: { replayTimeMs?: number | null; onMetrics?: (metrics: RenderMetrics) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const renderer = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const world = useRef<THREE.Group | null>(null);
  const [error, setError] = useState<string | null>(null);
  const twin = useGcsStore((state) => state.digitalTwin);
  const targets = useGcsStore((state) => state.targets);
  const plan = useGcsStore((state) => state.coveragePlan);
  const selectedVehicleId = useGcsStore((state) => state.selectedVehicleId);
  const selectedObservationId = useGcsStore((state) => state.selectedObservationId);
  const scaleMode = useGcsStore((state) => state.scaleMode);
  const replay = useGcsStore((state) => state.replay);
  const layers = useGcsStore((state) => state.digitalTwinLayers);

  useEffect(() => {
    if (!host.current) return;
    let animation = 0; let resize: ResizeObserver | null = null;
    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x071211); scene.fog = new THREE.Fog(0x071211, 100, 800);
    const camera = new THREE.PerspectiveCamera(48, 1, .02, 5000); camera.position.set(45, 42, 55); cameraRef.current = camera;
    try {
      const render = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true }); renderer.current = render;
      render.setPixelRatio(Math.min(window.devicePixelRatio, 2)); render.outputColorSpace = THREE.SRGBColorSpace;
      host.current.appendChild(render.domElement);
      const controls = new OrbitControls(camera, render.domElement); controls.enableDamping = true; controls.target.set(0, 0, 0);
      controlsRef.current = controls;
      scene.add(new THREE.HemisphereLight(0xbde9dc, 0x10201d, 2.5));
      const sun = new THREE.DirectionalLight(0xffffff, 2); sun.position.set(30, 60, 20); scene.add(sun);
      const grid = new THREE.GridHelper(400, 80, 0x34544d, 0x18302c); scene.add(grid);
      const axes = new THREE.AxesHelper(8); axes.name = "ENU DISPLAY AXES: X EAST / Y UP / -Z NORTH"; scene.add(axes);
      const group = new THREE.Group(); scene.add(group); world.current = group;
      let frames = 0; let measuredAt = performance.now(); let previousFrame = measuredAt; let frameTimes: number[] = [];
      const tick = (now: number) => {
        controls.update(); render.render(scene, camera); frames += 1;
        frameTimes.push(now - previousFrame); previousFrame = now;
        if (now - measuredAt >= 1000) {
          const fps = frames * 1000 / (now - measuredAt);
          const memory = (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory;
          onMetrics?.({ fps, frameTimeMs: 1000 / fps, frameTimeP50Ms: percentile(frameTimes, .5), frameTimeP95Ms: percentile(frameTimes, .95), objects: scene.children.reduce((count, child) => count + 1 + child.children.length, 0), memoryMb: memory ? memory.usedJSHeapSize / 1048576 : null });
          frames = 0; measuredAt = now; frameTimes = [];
        }
        animation = requestAnimationFrame(tick);
      };
      resize = new ResizeObserver(() => {
        if (!host.current) return; const width = host.current.clientWidth; const height = host.current.clientHeight;
        render.setSize(width, height, false); camera.aspect = width / Math.max(1, height); camera.updateProjectionMatrix();
      });
      resize.observe(host.current); animation = requestAnimationFrame(tick);
      return () => {
        cancelAnimationFrame(animation); resize?.disconnect(); controls.dispose(); disposeTree(scene); render.dispose();
        render.renderLists.dispose(); render.dispose(); render.forceContextLoss(); render.domElement.remove(); renderer.current = null; cameraRef.current = null; controlsRef.current = null; world.current = null;
      };
    } catch (reason) { setError(reason instanceof Error ? reason.message : "WEBGL INITIALIZATION FAILED"); }
  }, [onMetrics]);

  useEffect(() => {
    const group = world.current; if (!group) return;
    for (const child of [...group.children]) { disposeTree(child); group.remove(child); }
    if (!twin) return;
    const displayScale = scaleMode === "TRUE_SCALE" ? 1 : 3;
    const cameraConfig = twin.camera;
    const renderedVehicles = replayTimeMs != null && replay
      ? twin.vehicles.map((vehicle) => {
          const sample = interpolateReplaySample(replay.samples, vehicle.vehicle_id, replayTimeMs);
          return sample ? { ...vehicle, position_enu_m: sample.position_enu_m, position_wgs84: sample.position_wgs84, quaternion_body_to_enu: sample.quaternion_body_to_enu, telemetry_timestamp: sample.timestamp, stale: false } : { ...vehicle, stale: true };
        }) : twin.vehicles;
    for (const vehicle of renderedVehicles) {
      if (layers.aircraft) {
        const craft = aircraftModel(displayScale, vehicle.vehicle_id === selectedVehicleId);
        craft.position.set(...enuToThree(vehicle.position_enu_m));
        craft.setRotationFromMatrix(matrix4(bodyQuaternionToThreeRotation(vehicle.quaternion_body_to_enu)));
        craft.userData = { vehicleId: vehicle.vehicle_id, evidence: vehicle.evidence_class }; group.add(craft);
        if (layers.axes && vehicle.vehicle_id === selectedVehicleId) {
          craft.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 2 * displayScale, 0xef4444));
          craft.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 2 * displayScale, 0x22c55e));
          craft.add(new THREE.ArrowHelper(new THREE.Vector3(0, -1, 0), new THREE.Vector3(), 2 * displayScale, 0x60a5fa));
        }
      }
      if (vehicle.vehicle_id === selectedVehicleId) {
        const pose = cameraWorldPose(vehicle.position_enu_m, vehicle.quaternion_body_to_enu, cameraConfig.extrinsics);
        if (layers.cameraFrustum) {
          const frustum = cameraFrustum(cameraConfig.calibration, pose, Math.max(3, vehicle.position_enu_m[2] * 1.25));
          frustum.world.forEach((corner) => group.add(line([pose.originEnuM, corner], 0x60a5fa)));
          group.add(line([...frustum.world, frustum.world[0]], 0x60a5fa));
          const optical = cameraRayToWorld(pose, [0, 0, 1]);
          group.add(line([pose.originEnuM, [pose.originEnuM[0] + optical[0] * 8, pose.originEnuM[1] + optical[1] * 8, pose.originEnuM[2] + optical[2] * 8]], 0xffffff));
        }
        if (layers.cameraFootprint) {
          const footprint = cameraGroundFootprint(cameraConfig.calibration, pose, twin.ground.elevation_up_m);
          if (footprint.length === 4) group.add(line([...footprint.map((item) => item.pointEnuM), footprint[0].pointEnuM], 0x54e7b1, true));
        }
      }
    }
    if (layers.targets) for (const target of Object.values(targets)) {
      if (target.state === "REJECTED" || target.state === "CANDIDATE") continue;
      if (layers.uncertainty) {
        const geometry = new THREE.CircleGeometry(Math.max(.2, target.geolocation_uncertainty_m), 40);
        const material = new THREE.MeshBasicMaterial({ color: target.state === "DELIVERED" ? 0x60a5fa : 0xfb7185, transparent: true, opacity: .18, side: THREE.DoubleSide });
        const disk = new THREE.Mesh(geometry, material); disk.rotation.x = -Math.PI / 2; disk.position.set(...enuToThree([target.east_m, target.north_m, .03])); group.add(disk);
      }
      const selected = !useGcsStore.getState().selectedTargetId || useGcsStore.getState().selectedTargetId === target.target_id;
      const marker = new THREE.Mesh(new THREE.ConeGeometry(.4, 1.4, 5), new THREE.MeshStandardMaterial({ color: 0xfb7185, transparent: !selected, opacity: selected ? 1 : .35 }));
      marker.position.set(...enuToThree([target.east_m, target.north_m, .7])); group.add(marker);
    }
    const observation = twin.observations.find((item) => item.observation_id === selectedObservationId) ?? twin.observations.at(-1);
    if (layers.detectionRays && observation?.camera_origin_enu_m) group.add(line([observation.camera_origin_enu_m, observation.target_enu_m], 0xf97316));
    const searchAltitude = plan?.configuration.search_altitude_m ?? 0;
    if (layers.paths) for (const segment of plan?.segments ?? []) {
      if (segment.points_enu.length > 1) group.add(line(segment.points_enu.map((point) => [point.east_m, point.north_m, searchAltitude]), segment.kind === "SEARCH" ? 0x54e7b1 : 0xf8c15c, segment.kind === "TRANSIT"));
    }
    const zone = twin.launch_landing_zone;
    const outline: Vec3[] = [[-zone.width_m / 2, -zone.length_m / 2, .01], [zone.width_m / 2, -zone.length_m / 2, .01], [zone.width_m / 2, zone.length_m / 2, .01], [-zone.width_m / 2, zone.length_m / 2, .01], [-zone.width_m / 2, -zone.length_m / 2, .01]];
    group.add(line(outline, 0xf8c15c, true));
  }, [twin, targets, plan, selectedVehicleId, selectedObservationId, scaleMode, replayTimeMs, replay, layers]);

  const cameraPreset = (preset: string) => {
    const camera = cameraRef.current; const controls = controlsRef.current; if (!camera || !controls) return;
    const vehicle = twin?.vehicles.find((item) => item.vehicle_id === selectedVehicleId) ?? twin?.vehicles[0];
    const position = vehicle ? enuToThree(vehicle.position_enu_m) : [0, 0, 0] as Vec3;
    controls.target.set(...position);
    const offsets: Record<string, Vec3> = { TOP: [0, 65, 0.01], CHASE: [-22, 12, 0], ORBIT: [28, 24, 28], SIDE: [0, 12, 32], FIRST_PERSON: [0, 0, 0.3], RESET: [45, 42, 55], FREE: [45, 42, 55] };
    const offset = offsets[preset] ?? offsets.FREE; camera.position.set(position[0] + offset[0], position[1] + offset[1], position[2] + offset[2]); controls.update();
  };
  const exportSnapshot = () => {
    const canvas = renderer.current?.domElement; if (!canvas || !twin) return;
    const stamp = new Date().toISOString().replaceAll(":", "-");
    const download = (href: string, name: string) => { const link = document.createElement("a"); link.href = href; link.download = name; link.click(); };
    download(canvas.toDataURL("image/png"), `rescueswarm_${twin.mission_id}_${stamp}.png`);
    const metadata = new Blob([JSON.stringify({ mission_id: twin.mission_id, timestamp: new Date().toISOString(), execution_source: twin.execution_source, vehicles: twin.vehicles, target_ids: Object.keys(targets) }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(metadata); download(url, `rescueswarm_${twin.mission_id}_${stamp}.json`); URL.revokeObjectURL(url);
  };
  return <div className="digital-twin-shell" ref={host} aria-label="Three-dimensional RescueSwarm digital twin">
    <div className="camera-presets">{["TOP", "CHASE", "ORBIT", "SIDE", "FIRST_PERSON", "FREE", "RESET"].map((preset) => <button key={preset} onClick={() => cameraPreset(preset)}>{preset === "CHASE" ? "FOLLOW DRONE" : preset === "RESET" ? "RESET VIEW" : preset.replace("_", " ")}</button>)}<button onClick={exportSnapshot}>EXPORT SNAPSHOT</button></div>
    <div className="orientation-gizmo" aria-label="World orientation: north, east, up"><b>N ↑</b><span>E →</span><span>UP ⊙</span></div>
    <div className="digital-twin-badge">DIGITAL TWIN · {twin?.execution_source ?? "NO DATA"} · METRES · FLAT GROUND</div>
    {!twin && <div className="digital-twin-empty">NO DIGITAL-TWIN SNAPSHOT</div>}
    {error && <div className="map-error">3D UNAVAILABLE · {error}</div>}
  </div>;
}
