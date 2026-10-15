import { useEffect, useRef } from "react";
import * as THREE from "three";

import {
  add, bodyNedQuaternionToEnu, bodyQuaternionToThreeRotation, enuToThree,
  quaternionMultiply, rotateVector, scale, type Quaternion, type Vec3,
} from "../spatial/frames";

export interface PreviewSurvivor { id: string; eastM: number; northM: number; bodyState: "standing" | "lying"; }

interface Props {
  boundary: [number, number][];
  survivors: PreviewSurvivor[];
  bodySize: [number, number, number];
  armLength: number;
  rotorCount: number;
  cameraIntrinsics: { fx: number; fy: number; cx: number; cy: number; width: number; height: number };
  cameraMount: [number, number, number];
  cameraQuaternionToBody: Quaternion;
  livePose?: { positionEnuM: Vec3; quaternionBodyToNed: Quaternion } | null;
  onGroundPoint?: (eastM: number, northM: number) => void;
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    materials.forEach((material) => material.dispose());
  });
}

/** Configuration-only preview. Live runs switch to MAVLink estimated pose. */
export function SimulationWorldPreview({ boundary, survivors, bodySize, armLength, rotorCount, cameraIntrinsics, cameraMount, cameraQuaternionToBody, livePose, onGroundPoint }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const clickCallback = useRef(onGroundPoint);
  clickCallback.current = onGroundPoint;

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x071312);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 1500);
    camera.position.set(32, 34, 38);
    camera.lookAt(0, 0, 0);
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    element.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xc8ece5, 0x24362d, 2.2));
    const grid = new THREE.GridHelper(100, 20, 0x3b6b61, 0x19332e);
    scene.add(grid);
    const axes = new THREE.AxesHelper(5);
    scene.add(axes);

    if (boundary.length >= 3) {
      const points = [...boundary, boundary[0]].map(([east, north]) => {
        const [x, y, z] = enuToThree([east, north, 0.03]);
        return new THREE.Vector3(x, y, z);
      });
      scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: 0x62e6bd })));
    }

    const aircraft = new THREE.Group();
    const [length, width, height] = bodySize;
    const body = new THREE.Mesh(new THREE.BoxGeometry(length, height, width), new THREE.MeshStandardMaterial({ color: 0x1f8b70, roughness: .7 }));
    aircraft.add(body);
    const safeRotors = Math.max(1, rotorCount);
    for (let index = 0; index < safeRotors; index += 1) {
      const angle = index / safeRotors * Math.PI * 2 + Math.PI / 4;
      const arm = new THREE.Mesh(new THREE.BoxGeometry(armLength, .018, .025), new THREE.MeshStandardMaterial({ color: 0x8ea8a1 }));
      arm.position.set(Math.cos(angle) * armLength / 2, 0, Math.sin(angle) * armLength / 2);
      arm.rotation.y = -angle;
      aircraft.add(arm);
      const rotor = new THREE.Mesh(new THREE.CylinderGeometry(.13, .13, .008, 24), new THREE.MeshStandardMaterial({ color: index % 2 ? 0xe6ad4e : 0xc4d8d3, transparent: true, opacity: .72 }));
      rotor.position.set(Math.cos(angle) * armLength, .02, Math.sin(angle) * armLength);
      aircraft.add(rotor);
    }
    const bodyPositionEnu: Vec3 = livePose?.positionEnuM ?? [0, 0, 8];
    const bodyQuaternionEnu = bodyNedQuaternionToEnu(livePose?.quaternionBodyToNed ?? [1, 0, 0, 0]);
    const [aircraftX, aircraftY, aircraftZ] = enuToThree(bodyPositionEnu);
    aircraft.position.set(aircraftX, aircraftY, aircraftZ);
    const rotation = bodyQuaternionToThreeRotation(bodyQuaternionEnu);
    aircraft.setRotationFromMatrix(new THREE.Matrix4().set(
      rotation[0][0], rotation[0][1], rotation[0][2], 0,
      rotation[1][0], rotation[1][1], rotation[1][2], 0,
      rotation[2][0], rotation[2][1], rotation[2][2], 0,
      0, 0, 0, 1,
    ));
    scene.add(aircraft);

    const cameraOriginEnu = add(bodyPositionEnu, rotateVector(bodyQuaternionEnu, cameraMount));
    const cameraQuaternionEnu = quaternionMultiply(bodyQuaternionEnu, cameraQuaternionToBody);
    const cameraOrigin = new THREE.Vector3(...enuToThree(cameraOriginEnu));
    const cornerPixels: [number, number][] = [[0, 0], [cameraIntrinsics.width, 0], [cameraIntrinsics.width, cameraIntrinsics.height], [0, cameraIntrinsics.height]];
    const footprint: THREE.Vector3[] = [];
    const rays: THREE.Vector3[] = [];
    cornerPixels.forEach(([u, v]) => {
      const optical = [(u - cameraIntrinsics.cx) / cameraIntrinsics.fx, (v - cameraIntrinsics.cy) / cameraIntrinsics.fy, 1] as Vec3;
      const rayEnu = rotateVector(cameraQuaternionEnu, optical);
      if (rayEnu[2] >= -1e-6) return;
      const groundEnu = add(cameraOriginEnu, scale(rayEnu, -cameraOriginEnu[2] / rayEnu[2]));
      const point = new THREE.Vector3(...enuToThree(groundEnu));
      footprint.push(point); rays.push(cameraOrigin.clone(), point);
    });
    scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(rays), new THREE.LineBasicMaterial({ color: 0x5db7e8, transparent: true, opacity: .7 })));
    if (footprint.length >= 3) scene.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(footprint), new THREE.LineBasicMaterial({ color: 0x5db7e8 })));

    survivors.forEach((survivor) => {
      const person = new THREE.Group();
      const torso = new THREE.Mesh(new THREE.CapsuleGeometry(.16, .85, 4, 10), new THREE.MeshStandardMaterial({ color: 0xf36d2d }));
      torso.position.y = .62;
      person.add(torso);
      const head = new THREE.Mesh(new THREE.SphereGeometry(.14, 12, 8), new THREE.MeshStandardMaterial({ color: 0xd5a079 }));
      head.position.y = 1.35;
      person.add(head);
      if (survivor.bodyState === "lying") { person.rotation.z = Math.PI / 2; person.position.y = .18; }
      const [x, y, z] = enuToThree([survivor.eastM, survivor.northM, 0]);
      person.position.x += x; person.position.y += y; person.position.z += z;
      scene.add(person);
    });

    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handleClick = (event: MouseEvent) => {
      if (!clickCallback.current) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const point = new THREE.Vector3();
      if (raycaster.ray.intersectPlane(groundPlane, point)) clickCallback.current(point.x, -point.z);
    };
    renderer.domElement.addEventListener("click", handleClick);
    let animation = 0;
    const resize = () => {
      const widthPx = Math.max(1, element.clientWidth); const heightPx = Math.max(1, element.clientHeight);
      renderer.setSize(widthPx, heightPx, false); camera.aspect = widthPx / heightPx; camera.updateProjectionMatrix();
    };
    const render = () => { resize(); renderer.render(scene, camera); animation = requestAnimationFrame(render); };
    render();
    return () => {
      cancelAnimationFrame(animation);
      renderer.domElement.removeEventListener("click", handleClick);
      disposeObject(scene); renderer.renderLists.dispose(); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
    };
  }, [boundary, survivors, bodySize, armLength, rotorCount, cameraIntrinsics, cameraMount, cameraQuaternionToBody, livePose]);

  return <div className="simulation-preview" ref={host} role="img" aria-label="Simulation configuration 3D preview" />;
}
