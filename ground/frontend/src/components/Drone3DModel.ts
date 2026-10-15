import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export interface LoadedDrone {
  group: THREE.Group;
  rotors: THREE.Object3D[];
  updateRotors: (deltaSec: number, isArmed: boolean) => void;
  dispose: () => void;
  isCustomModel: boolean;
}

export interface LoadedHuman {
  group: THREE.Group;
  dispose: () => void;
  isCustomModel: boolean;
}

function disposeHierarchy(root: THREE.Object3D) {
  root.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      mesh.geometry?.dispose();
      if (Array.isArray(mesh.material)) {
        mesh.material.forEach((mat) => mat.dispose());
      } else if (mesh.material) {
        mesh.material.dispose();
      }
    }
  });
}

/**
 * Creates a high-fidelity procedural quadcopter fallback if GLTF loading fails.
 */
export function createProceduralDrone(): LoadedDrone {
  const group = new THREE.Group();
  group.name = "FallbackProceduralDrone";

  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x1a453b,
    roughness: 0.4,
    metalness: 0.6,
  });
  const armMat = new THREE.MeshStandardMaterial({
    color: 0x0f231e,
    roughness: 0.7,
    metalness: 0.8,
  });
  const rotorMat = new THREE.MeshStandardMaterial({
    color: 0x54e7b1,
    roughness: 0.3,
    metalness: 0.2,
    transparent: true,
    opacity: 0.85,
  });
  const accentMat = new THREE.MeshStandardMaterial({
    color: 0xfacc15,
    roughness: 0.2,
    metalness: 0.1,
  });

  // Central body
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.09, 0.28), bodyMat);
  body.castShadow = true;
  body.receiveShadow = true;
  body.position.y = 0.08;
  group.add(body);

  // Top canopy
  const canopy = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.18, 4), accentMat);
  canopy.rotation.z = -Math.PI / 2;
  canopy.rotation.y = Math.PI / 4;
  canopy.position.set(0.12, 0.11, 0);
  group.add(canopy);

  // Arms & Rotors
  const rotorObjects: THREE.Object3D[] = [];
  const armOffset = 0.26;
  const positions: [number, number][] = [
    [armOffset, armOffset],
    [-armOffset, armOffset],
    [-armOffset, -armOffset],
    [armOffset, -armOffset],
  ];

  positions.forEach(([x, z], idx) => {
    const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, armOffset * 1.4, 8), armMat);
    arm.rotation.z = Math.PI / 2;
    arm.rotation.y = Math.atan2(z, x);
    arm.position.set(x / 2, 0.08, z / 2);
    arm.castShadow = true;
    group.add(arm);

    const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.04, 16), bodyMat);
    motor.position.set(x, 0.09, z);
    motor.castShadow = true;
    group.add(motor);

    const rotorGroup = new THREE.Group();
    rotorGroup.name = `motor_top${idx + 1}`;
    rotorGroup.position.set(x, 0.12, z);

    const blade1 = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.005, 0.025), rotorMat);
    blade1.castShadow = true;
    const blade2 = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.005, 0.24), rotorMat);
    blade2.castShadow = true;
    rotorGroup.add(blade1, blade2);

    group.add(rotorGroup);
    rotorObjects.push(rotorGroup);
  });

  // Landing skids
  for (const z of [-0.1, 0.1]) {
    const skid = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.36, 8), armMat);
    skid.rotation.x = Math.PI / 2;
    skid.position.set(0, 0.02, z);
    skid.castShadow = true;
    group.add(skid);

    for (const x of [-0.1, 0.1]) {
      const strut = new THREE.Mesh(new THREE.CylinderGeometry(0.007, 0.007, 0.07, 8), armMat);
      strut.position.set(x, 0.05, z);
      strut.castShadow = true;
      group.add(strut);
    }
  }

  let spinAngle = 0;
  const updateRotors = (deltaSec: number, isArmed: boolean) => {
    if (!isArmed) return;
    spinAngle += deltaSec * 50;
    rotorObjects.forEach((rotor, idx) => {
      const dir = idx % 2 === 0 ? 1 : -1;
      rotor.rotation.y = spinAngle * dir;
    });
  };

  const dispose = () => {
    disposeHierarchy(group);
  };

  return {
    group,
    rotors: rotorObjects,
    updateRotors,
    dispose,
    isCustomModel: false,
  };
}

let cachedDroneGltf: any = null;
let cachedPersonGltf: any = null;

/**
 * Loads the user's actual 3D drone model from SURVIVOR_DETECTION/3D_SIMULATION_MAIN/drone/scene.gltf
 * Scaled to typical industrial medical-servicing drone dimensions (~1.75m span).
 */
export async function loadDroneModel(targetSpanMeters = 1.75): Promise<LoadedDrone> {
  const loader = new GLTFLoader();
  const modelUrl = "/models/drone/scene.gltf";

  try {
    let gltf = cachedDroneGltf;
    if (!gltf) {
      gltf = await loader.loadAsync(modelUrl);
      cachedDroneGltf = gltf;
    }

    const rawScene = gltf.scene.clone(true) as THREE.Group;
    rawScene.name = "ActualDroneModel";

    // Locate only the 4 top-level rotor nodes for smooth animation
    const rotorMeshes: THREE.Object3D[] = [];
    rawScene.traverse((child) => {
      // Exactly match motor_top1, motor_top2, motor_top3, motor_top4 (avoiding child sub-meshes)
      if (child.name && /^motor_top[1-4]$/i.test(child.name)) {
        rotorMeshes.push(child);
      }
      if ((child as THREE.Mesh).isMesh) {
        // Ground shadow is provided by smooth circular contact shadow (groundShadowRef).
        // Disabling receiveShadow on 350 internal sub-meshes avoids massive GPU shadow map evaluation.
        child.castShadow = true;
        child.receiveShadow = false;
      }
      // Optimize static nodes: disable per-frame matrix recalculation for non-rotor parts
      if (!/^motor_top/i.test(child.name) && child !== rawScene) {
        child.matrixAutoUpdate = false;
        child.updateMatrix();
      }
    });

    // Compute bounding box to normalize scale, center, and ground contact
    const bbox = new THREE.Box3().setFromObject(rawScene);
    const size = new THREE.Vector3();
    bbox.getSize(size);
    const center = new THREE.Vector3();
    bbox.getCenter(center);

    const maxHorizontalSpan = Math.max(size.x, size.z);
    const scaleFactor = maxHorizontalSpan > 0 ? targetSpanMeters / maxHorizontalSpan : 0.035;

    const wrapper = new THREE.Group();
    wrapper.name = "Drone3DRoot";

    // Center horizontally and align bottom of landing skids flush with ground (y = 0)
    rawScene.position.set(-center.x, -bbox.min.y, -center.z);

    const orientedGroup = new THREE.Group();
    orientedGroup.name = "DroneOrientedInner";
    orientedGroup.add(rawScene);
    orientedGroup.scale.set(scaleFactor, scaleFactor, scaleFactor);
    wrapper.add(orientedGroup);

    console.log(
      `[Drone3DModel] Loaded actual drone model. Span: ${targetSpanMeters}m (scale: ${scaleFactor.toFixed(5)}), rotors: ${rotorMeshes.length}`
    );

    let rotorAngle = 0;
    const updateRotors = (deltaSec: number, isArmed: boolean) => {
      if (!isArmed && rotorMeshes.length > 0) return;
      rotorAngle += deltaSec * 50;
      rotorMeshes.forEach((rotor, idx) => {
        const direction = idx % 2 === 0 ? 1 : -1;
        rotor.rotation.y = rotorAngle * direction;
      });
    };

    const dispose = () => {
      disposeHierarchy(wrapper);
    };

    return {
      group: wrapper,
      rotors: rotorMeshes,
      updateRotors,
      dispose,
      isCustomModel: true,
    };
  } catch (err) {
    console.warn("[Drone3DModel] Could not load actual drone GLTF, using procedural fallback:", err);
    const proc = createProceduralDrone();
    const factor = targetSpanMeters / 0.76;
    proc.group.scale.set(factor, factor, factor);
    return proc;
  }
}

/**
 * Creates a crisp floating billboard canvas text sprite for labels.
 */
export function createFloatingLabel(text: string, colorHex = "#f59e0b"): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 384;
  canvas.height = 80;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "rgba(10, 24, 20, 0.88)";
    ctx.strokeStyle = colorHex;
    ctx.lineWidth = 4;
    ctx.roundRect(4, 4, 376, 72, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 26px -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 192, 40);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const spriteMat = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
  });
  const sprite = new THREE.Sprite(spriteMat);
  sprite.scale.set(3.8, 0.8, 1);
  return sprite;
}

let cachedTerrainTexture: THREE.CanvasTexture | null = null;

/**
 * Creates subtle, natural organic terrain noise texture for physical outdoor grounding.
 * Caches the texture instance to prevent expensive per-mount canvas drawing.
 */
export function createTerrainTexture(): THREE.CanvasTexture {
  if (cachedTerrainTexture) return cachedTerrainTexture;
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "#11261f";
    ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 2000; i++) {
      const x = Math.random() * 256;
      const y = Math.random() * 256;
      const r = Math.random() * 3 + 1;
      const alpha = Math.random() * 0.14;
      ctx.fillStyle = Math.random() > 0.5 ? `rgba(28, 62, 49, ${alpha})` : `rgba(9, 21, 16, ${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(60, 60);
  cachedTerrainTexture = texture;
  return texture;
}

/**
 * Loads the user's actual human model from SURVIVOR_DETECTION/3D_SIMULATION_MAIN/person/scene.gltf
 */
export async function loadHumanModel(
  bodyState: "standing" | "lying" = "standing",
  id = "survivor"
): Promise<LoadedHuman> {
  const loader = new GLTFLoader();
  const modelUrl = "/models/person/scene.gltf";

  try {
    let gltf = cachedPersonGltf;
    if (!gltf) {
      gltf = await loader.loadAsync(modelUrl);
      cachedPersonGltf = gltf;
    }

    const rawScene = gltf.scene.clone(true) as THREE.Group;
    rawScene.name = `ActualHumanModel_${id}_${bodyState}`;

    rawScene.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        child.castShadow = true;
        child.receiveShadow = false;
        child.matrixAutoUpdate = false;
        child.updateMatrix();
      }
    });

    const wrapper = new THREE.Group();
    wrapper.name = `HumanRoot_${id}_${bodyState}`;

    // Target human physical height: 1.72m (~5 ft 7.7 in, typical adult SAR subject)
    const targetHeight = 1.72;
    const orientedGroup = new THREE.Group();
    orientedGroup.name = `HumanOriented_${id}_${bodyState}`;

    if (bodyState === "standing") {
      // In raw GLTF scene (+Y is UP, feet at y=0, head at y=1.9004m, X is lateral width, Z is sagittal depth).
      // The person is already standing upright. Preserve uniform scaling, aspect ratio, and orientation.
      rawScene.updateMatrixWorld(true);

      const bbox = new THREE.Box3().setFromObject(rawScene);
      const size = new THREE.Vector3();
      bbox.getSize(size);
      const center = new THREE.Vector3();
      bbox.getCenter(center);

      const unscaledHeight = Math.max(size.y, 0.01);
      const scaleFactor = targetHeight / unscaledHeight;

      // Center horizontally and align feet flush with ground (y = 0)
      rawScene.position.set(-center.x, -bbox.min.y, -center.z);

      orientedGroup.add(rawScene);
      orientedGroup.scale.set(scaleFactor, scaleFactor, scaleFactor);
    } else {
      // Lying down: rotate around X by -Math.PI / 2 so the longitudinal body axis (+Y)
      // lies horizontal along the ground plane (Z).
      rawScene.rotation.x = -Math.PI / 2;
      rawScene.updateMatrixWorld(true);

      const bbox = new THREE.Box3().setFromObject(rawScene);
      const size = new THREE.Vector3();
      bbox.getSize(size);
      const center = new THREE.Vector3();
      bbox.getCenter(center);

      // Body length along ground is in Z (derived from unscaled height 1.9004m)
      // Maintain the correct physical body length (1.72m), not vertical bounding box thickness.
      const unscaledBodyLength = Math.max(size.z, 0.01);
      const scaleFactor = targetHeight / unscaledBodyLength;

      // Center horizontally and align back resting slightly above ground (y = 0.02m)
      rawScene.position.set(-center.x, -bbox.min.y + 0.02, -center.z);

      orientedGroup.add(rawScene);
      orientedGroup.scale.set(scaleFactor, scaleFactor, scaleFactor);
    }

    wrapper.add(orientedGroup);

    const dispose = () => {
      disposeHierarchy(wrapper);
    };

    return {
      group: wrapper,
      dispose,
      isCustomModel: true,
    };
  } catch (err) {
    console.warn("[Drone3DModel] Could not load actual human GLTF, using procedural human marker:", err);
    const wrapper = new THREE.Group();
    const isLying = bodyState === "lying";
    const mat = new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      roughness: 0.5,
      metalness: 0.1,
    });
    // Physically consistent procedural human (height = 1.72m)
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.13, 16, 16), mat);
    head.position.y = 1.59;
    head.castShadow = true;
    const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.14, 0.68, 12), mat);
    torso.position.y = 1.10;
    torso.castShadow = true;
    const legs = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.09, 0.76, 12), mat);
    legs.position.y = 0.38;
    legs.castShadow = true;
    wrapper.add(head, torso, legs);

    if (isLying) {
      wrapper.rotation.x = Math.PI / 2;
      wrapper.position.y = 0.18;
    }

    return {
      group: wrapper,
      dispose: () => disposeHierarchy(wrapper),
      isCustomModel: false,
    };
  }
}

/**
 * Creates a high-visibility 3D medical kit (SAR payload) model resting at a delivery point.
 * Accurately scaled (~0.35m x 0.25m x 0.16m) with medical cross markings and handle.
 */
export function createMedicalKitModel(): THREE.Group {
  const kitGroup = new THREE.Group();
  kitGroup.name = "MedicalKitPayload";

  // Rugged case body (High-vis Emergency Red)
  const caseMat = new THREE.MeshStandardMaterial({
    color: 0xe11d48, // Vivid emergency red
    roughness: 0.35,
    metalness: 0.2,
  });
  const caseGeo = new THREE.BoxGeometry(0.36, 0.15, 0.26);
  const caseMesh = new THREE.Mesh(caseGeo, caseMat);
  caseMesh.position.y = 0.075;
  caseMesh.castShadow = true;
  caseMesh.receiveShadow = true;
  kitGroup.add(caseMesh);

  // White cross emblem background plate
  const emblemMat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.3,
  });
  const emblemGeo = new THREE.BoxGeometry(0.18, 0.005, 0.18);
  const emblemMesh = new THREE.Mesh(emblemGeo, emblemMat);
  emblemMesh.position.y = 0.152;
  kitGroup.add(emblemMesh);

  // Red Cross emblem (two perpendicular bars)
  const crossMat = new THREE.MeshStandardMaterial({
    color: 0xdc2626,
    roughness: 0.2,
  });
  const barV = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.006, 0.14), crossMat);
  barV.position.y = 0.155;
  const barH = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.006, 0.045), crossMat);
  barH.position.y = 0.155;
  kitGroup.add(barV, barH);

  // Carrying handle
  const handleMat = new THREE.MeshStandardMaterial({
    color: 0x1e293b,
    roughness: 0.5,
    metalness: 0.7,
  });
  const handleGeo = new THREE.CylinderGeometry(0.012, 0.012, 0.18, 8);
  const handleMesh = new THREE.Mesh(handleGeo, handleMat);
  handleMesh.rotation.z = Math.PI / 2;
  handleMesh.position.set(0, 0.075, 0.145);
  kitGroup.add(handleMesh);

  // Reflective yellow hazard stripes on edges
  const stripeMat = new THREE.MeshBasicMaterial({ color: 0xfacc15 });
  const stripeL = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.152, 0.262), stripeMat);
  stripeL.position.set(-0.16, 0.075, 0);
  const stripeR = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.152, 0.262), stripeMat);
  stripeR.position.set(0.16, 0.075, 0);
  kitGroup.add(stripeL, stripeR);

  return kitGroup;
}

/**
 * Creates a high-visibility rescue parachute model attached to falling medical payload.
 */
export function createParachuteModel(): THREE.Group {
  const chuteGroup = new THREE.Group();
  chuteGroup.name = "EmergencyParachute";

  // Orange & White Rescue Parachute Canopy
  const canopyMat = new THREE.MeshStandardMaterial({
    color: 0xf97316, // Emergency rescue orange
    roughness: 0.5,
    metalness: 0.05,
    side: THREE.DoubleSide,
  });
  // Upper dome canopy
  const canopyGeo = new THREE.SphereGeometry(0.65, 16, 12, 0, Math.PI * 2, 0, Math.PI * 0.45);
  const canopy = new THREE.Mesh(canopyGeo, canopyMat);
  canopy.position.y = 1.0;
  chuteGroup.add(canopy);

  // White center apex stripe / vent
  const ventGeo = new THREE.CylinderGeometry(0.12, 0.12, 0.04, 16);
  const ventMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.4 });
  const vent = new THREE.Mesh(ventGeo, ventMat);
  vent.position.y = 1.65;
  chuteGroup.add(vent);

  // 4 white suspension cords connecting canopy rim to kit top
  const cordMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 });
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  angles.forEach((ang) => {
    const rx = Math.cos(ang) * 0.65;
    const rz = Math.sin(ang) * 0.65;
    const pts = [
      new THREE.Vector3(rx, 1.0, rz),
      new THREE.Vector3(0, 0.18, 0),
    ];
    const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
    const line = new THREE.Line(lineGeo, cordMat);
    chuteGroup.add(line);
  });

  return chuteGroup;
}

