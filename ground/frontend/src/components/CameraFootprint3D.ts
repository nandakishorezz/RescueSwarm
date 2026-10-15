import * as THREE from "three";

export interface CameraFootprintVisualizer {
  group: THREE.Group;
  update: (
    dronePos: THREE.Vector3,
    yawDeg: number,
    pitchDeg: number,
    rollDeg: number,
    altitudeMeters: number
  ) => void;
  dispose: () => void;
}

export interface CameraFootprintOptions {
  hfovDeg?: number;
  vfovDeg?: number;
  color?: number;
  footprintColor?: number;
  opacity?: number;
}

/**
 * Creates the 3D Camera Footprint attached to the drone, projecting
 * the optical frustum and ground viewed area.
 * Marked as: SIMULATED / APPROXIMATE (per Section 7 requirements).
 */
export function createCameraFootprint(options: CameraFootprintOptions = {}): CameraFootprintVisualizer {
  const hfov = ((options.hfovDeg ?? 68) * Math.PI) / 180;
  const vfov = ((options.vfovDeg ?? 46) * Math.PI) / 180;
  const lineColor = options.color ?? 0x54e7b1;
  const fillColor = options.footprintColor ?? 0x34d399;

  const group = new THREE.Group();
  group.name = "CameraFootprintGroup";

  // Frustum ray lines (4 rays from camera to ground corners + 1 center ray)
  // 5 lines = 10 vertices
  const rayGeometry = new THREE.BufferGeometry();
  const rayPositions = new Float32Array(5 * 2 * 3);
  rayGeometry.setAttribute("position", new THREE.BufferAttribute(rayPositions, 3));
  const rayMaterial = new THREE.LineBasicMaterial({
    color: lineColor,
    transparent: true,
    opacity: 0.45,
  });
  const rayLines = new THREE.LineSegments(rayGeometry, rayMaterial);
  group.add(rayLines);

  // Ground footprint perimeter
  const perimeterGeometry = new THREE.BufferGeometry();
  const perimeterPositions = new Float32Array(5 * 3); // 4 corners + closed loop
  perimeterGeometry.setAttribute("position", new THREE.BufferAttribute(perimeterPositions, 3));
  const perimeterMaterial = new THREE.LineBasicMaterial({
    color: lineColor,
    transparent: true,
    opacity: 0.85,
    linewidth: 2,
  });
  const perimeterLine = new THREE.Line(perimeterGeometry, perimeterMaterial);
  group.add(perimeterLine);

  // Ground footprint filled polygon (2 triangles = 6 vertices)
  const fillGeometry = new THREE.BufferGeometry();
  const fillPositions = new Float32Array(6 * 3);
  fillGeometry.setAttribute("position", new THREE.BufferAttribute(fillPositions, 3));
  const fillMaterial = new THREE.MeshBasicMaterial({
    color: fillColor,
    transparent: true,
    opacity: 0.16,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const fillMesh = new THREE.Mesh(fillGeometry, fillMaterial);
  group.add(fillMesh);

  // Subtle interior grid (2 cross-lines = 4 vertices = 2 segments)
  const gridGeometry = new THREE.BufferGeometry();
  const gridPositions = new Float32Array(2 * 2 * 3);
  gridGeometry.setAttribute("position", new THREE.BufferAttribute(gridPositions, 3));
  const gridMaterial = new THREE.LineBasicMaterial({
    color: lineColor,
    transparent: true,
    opacity: 0.25,
  });
  const gridLines = new THREE.LineSegments(gridGeometry, gridMaterial);
  group.add(gridLines);

  // Pre-allocated reusable scratch objects to avoid per-frame GC allocations
  const scratchRawCorners = [
    new THREE.Vector3(),
    new THREE.Vector3(),
    new THREE.Vector3(),
    new THREE.Vector3(),
  ];
  const scratchCorners = [
    new THREE.Vector3(),
    new THREE.Vector3(),
    new THREE.Vector3(),
    new THREE.Vector3(),
  ];
  const scratchRotEuler = new THREE.Euler(0, 0, 0, "YXZ");
  const scratchCamPos = new THREE.Vector3();
  const scratchCenterGround = new THREE.Vector3();
  const scratchMid01 = new THREE.Vector3();
  const scratchMid23 = new THREE.Vector3();
  const scratchMid12 = new THREE.Vector3();
  const scratchMid30 = new THREE.Vector3();

  const update = (
    dronePos: THREE.Vector3,
    yawDeg: number,
    pitchDeg: number,
    rollDeg: number,
    altitudeMeters: number
  ) => {
    // If drone is on the ground, hide footprint to prevent clipping
    if (altitudeMeters < 0.15) {
      group.visible = false;
      return;
    }
    group.visible = true;

    const alt = Math.max(0.2, dronePos.y);
    const halfWidth = alt * Math.tan(hfov / 2);
    const halfHeight = alt * Math.tan(vfov / 2);

    // Drone yaw in radians (Three.js coordinates: 0 is North (-Z), 90 is East (+X))
    const yawRad = -yawDeg * (Math.PI / 180);
    const pitchRad = pitchDeg * (Math.PI / 180);
    const rollRad = rollDeg * (Math.PI / 180);

    // Set ground corners in body-relative coordinates without new allocations
    scratchRawCorners[0].set(-halfWidth, 0, -halfHeight); // Front-Left
    scratchRawCorners[1].set(halfWidth, 0, -halfHeight);  // Front-Right
    scratchRawCorners[2].set(halfWidth, 0, halfHeight);   // Back-Right
    scratchRawCorners[3].set(-halfWidth, 0, halfHeight);  // Back-Left

    // Apply rotation
    scratchRotEuler.set(pitchRad, yawRad, rollRad, "YXZ");
    const groundY = 0.02;

    for (let i = 0; i < 4; i++) {
      scratchCorners[i].copy(scratchRawCorners[i]).applyEuler(scratchRotEuler);
      scratchCorners[i].x += dronePos.x;
      scratchCorners[i].y = groundY;
      scratchCorners[i].z += dronePos.z;
    }

    scratchCamPos.set(dronePos.x, dronePos.y - 0.05, dronePos.z);
    scratchCenterGround.set(dronePos.x, groundY, dronePos.z);

    // 1. Ray lines: camPos -> corners[0..3] and camPos -> centerGround
    const rayPosAttr = rayGeometry.attributes.position as THREE.BufferAttribute;
    const rArr = rayPosAttr.array as Float32Array;
    let rIdx = 0;
    for (let i = 0; i < 4; i++) {
      rArr[rIdx++] = scratchCamPos.x; rArr[rIdx++] = scratchCamPos.y; rArr[rIdx++] = scratchCamPos.z;
      rArr[rIdx++] = scratchCorners[i].x; rArr[rIdx++] = scratchCorners[i].y; rArr[rIdx++] = scratchCorners[i].z;
    }
    // Center ray
    rArr[rIdx++] = scratchCamPos.x; rArr[rIdx++] = scratchCamPos.y; rArr[rIdx++] = scratchCamPos.z;
    rArr[rIdx++] = scratchCenterGround.x; rArr[rIdx++] = scratchCenterGround.y; rArr[rIdx++] = scratchCenterGround.z;
    rayPosAttr.needsUpdate = true;

    // 2. Perimeter lines: corner[0] -> [1] -> [2] -> [3] -> [0]
    const perimPosAttr = perimeterGeometry.attributes.position as THREE.BufferAttribute;
    const pArr = perimPosAttr.array as Float32Array;
    let pIdx = 0;
    for (let i = 0; i <= 4; i++) {
      const c = scratchCorners[i % 4];
      pArr[pIdx++] = c.x; pArr[pIdx++] = c.y; pArr[pIdx++] = c.z;
    }
    perimPosAttr.needsUpdate = true;

    // 3. Filled polygon: 2 triangles (0, 1, 2) and (0, 2, 3)
    const fillPosAttr = fillGeometry.attributes.position as THREE.BufferAttribute;
    const fArr = fillPosAttr.array as Float32Array;
    const triIndices = [0, 1, 2, 0, 2, 3];
    let fIdx = 0;
    for (const idx of triIndices) {
      const c = scratchCorners[idx];
      fArr[fIdx++] = c.x; fArr[fIdx++] = c.y; fArr[fIdx++] = c.z;
    }
    fillPosAttr.needsUpdate = true;

    // 4. Subtle interior cross grid: mid(0,1) -> mid(2,3) and mid(1,2) -> mid(3,0)
    const gridPosAttr = gridGeometry.attributes.position as THREE.BufferAttribute;
    const gArr = gridPosAttr.array as Float32Array;
    scratchMid01.addVectors(scratchCorners[0], scratchCorners[1]).multiplyScalar(0.5);
    scratchMid23.addVectors(scratchCorners[2], scratchCorners[3]).multiplyScalar(0.5);
    scratchMid12.addVectors(scratchCorners[1], scratchCorners[2]).multiplyScalar(0.5);
    scratchMid30.addVectors(scratchCorners[3], scratchCorners[0]).multiplyScalar(0.5);

    gArr[0] = scratchMid01.x; gArr[1] = scratchMid01.y; gArr[2] = scratchMid01.z;
    gArr[3] = scratchMid23.x; gArr[4] = scratchMid23.y; gArr[5] = scratchMid23.z;
    gArr[6] = scratchMid12.x; gArr[7] = scratchMid12.y; gArr[8] = scratchMid12.z;
    gArr[9] = scratchMid30.x; gArr[10] = scratchMid30.y; gArr[11] = scratchMid30.z;
    gridPosAttr.needsUpdate = true;
  };

  const dispose = () => {
    rayGeometry.dispose();
    rayMaterial.dispose();
    perimeterGeometry.dispose();
    perimeterMaterial.dispose();
    fillGeometry.dispose();
    fillMaterial.dispose();
    gridGeometry.dispose();
    gridMaterial.dispose();
  };

  return {
    group,
    update,
    dispose,
  };
}
