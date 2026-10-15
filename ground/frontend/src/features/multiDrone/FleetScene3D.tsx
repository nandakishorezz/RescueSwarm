import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { loadDroneModel, type LoadedDrone } from "../../components/Drone3DModel";
import type { FleetVehicleView } from "./types";

export function FleetScene3D({ vehicles }: { vehicles: FleetVehicleView[] }) {
  const host = useRef<HTMLDivElement>(null);
  const models = useRef<Record<string, LoadedDrone>>({});
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const pending = useRef(new Set<string>());
  const [webGlAvailable, setWebGlAvailable] = useState(true);

  useEffect(() => {
    const element = host.current;
    if (!element) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      element.appendChild(renderer.domElement);
    } catch (err) {
      console.warn("[FleetScene3D] WebGL renderer initialization failed:", err);
      setWebGlAvailable(false);
      return;
    }

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b1925);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x486273, 2.2));
    const grid = new THREE.GridHelper(100, 20, 0x27536b, 0x1c3442);
    scene.add(grid);
    const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 1000);
    cameraRef.current = camera;
    camera.position.set(8, 10, 12);
    camera.lookAt(0, 0, 0);

    sceneRef.current = scene;

    const resize = () => {
      const width = element.clientWidth || 600;
      const height = element.clientHeight || 300;
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    renderer.setAnimationLoop(() => renderer.render(scene, camera));

    return () => {
      sceneRef.current = null;
      cameraRef.current = null;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      Object.values(models.current).forEach((model) => model.dispose());
      models.current = {};
      grid.geometry.dispose();
      renderer.dispose();
      if (element.contains(renderer.domElement)) {
        element.removeChild(renderer.domElement);
      }
    };
  }, []);

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const safeVehicles = vehicles ?? [];
    const origin = safeVehicles.find(
      (vehicle) =>
        vehicle?.telemetry &&
        Number.isFinite(vehicle.telemetry.latitude) &&
        Number.isFinite(vehicle.telemetry.longitude),
    )?.telemetry;
    if (!origin) return;

    safeVehicles.forEach(async (vehicle) => {
      if (
        !vehicle?.telemetry ||
        !Number.isFinite(vehicle.telemetry.latitude) ||
        !Number.isFinite(vehicle.telemetry.longitude)
      ) {
        return;
      }
      let model = models.current[vehicle.vehicle_id];
      if (!model && !pending.current.has(vehicle.vehicle_id)) {
        pending.current.add(vehicle.vehicle_id);
        try {
          model = await loadDroneModel(); // same GLTF asset and physical scale as Mission 3D
        } catch (err) {
          console.warn("[FleetScene3D] Failed to load drone model:", err);
        } finally {
          pending.current.delete(vehicle.vehicle_id);
        }
        if (!sceneRef.current || !model) {
          model?.dispose();
          return;
        }
        model.group.name = `Fleet_${vehicle.vehicle_id}`;
        models.current[vehicle.vehicle_id] = model;
        scene.add(model.group);
      }
      if (!model) return;
      const t = vehicle.telemetry;
      const cosLat = Math.cos((origin.latitude * Math.PI) / 180);
      model.group.position.set(
        (t.longitude - origin.longitude) * 111_320 * cosLat,
        Math.max(0, Number.isFinite(t.relative_altitude_m) ? t.relative_altitude_m : 0),
        -(t.latitude - origin.latitude) * 111_320,
      );
    });

    const camera = cameraRef.current;
    if (camera && safeVehicles.length > 1) {
      const first = safeVehicles[0]?.telemetry;
      const second = safeVehicles[1]?.telemetry;
      const horizontalSeparation =
        first &&
        second &&
        Number.isFinite(first.longitude) &&
        Number.isFinite(second.longitude) &&
        Number.isFinite(first.latitude) &&
        Number.isFinite(second.latitude)
          ? Math.hypot(
              (first.longitude - second.longitude) * 111_320 * Math.cos((first.latitude * Math.PI) / 180),
              (first.latitude - second.latitude) * 111_320,
            )
          : 0;
      const distance = Math.max(10, horizontalSeparation * 1.4);
      camera.position.set(distance * 0.6, distance * 0.7, distance * 0.8);
      camera.lookAt(0, 0, 0);
    }
  }, [vehicles]);

  if (!webGlAvailable) {
    return (
      <div className="fleet-scene fleet-map-empty" aria-label="3D telemetry fallback">
        3D view waiting for WebGL support
      </div>
    );
  }

  return <div ref={host} className="fleet-scene" aria-label="Live fleet 3D telemetry" />;
}
