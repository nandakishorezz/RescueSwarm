import maplibregl, { type CustomLayerInterface, type Map as MapLibreMap } from "maplibre-gl";
import * as THREE from "three";

import { useGcsStore } from "../stores/gcsStore";
import { bodyQuaternionToThreeRotation, type Matrix3 } from "../spatial/frames";

function rotationMatrix(value: Matrix3): THREE.Matrix4 {
  // MapLibre custom coordinates are east/south/up. The centralized Three
  // transform is east/up/-north; swap its display Y/Z into map Z/Y here.
  return new THREE.Matrix4().set(
    value[0][0], value[0][1], value[0][2], 0,
    -value[2][0], -value[2][1], -value[2][2], 0,
    value[1][0], value[1][1], value[1][2], 0,
    0, 0, 0, 1,
  );
}

export function createDigitalTwinLayer(): CustomLayerInterface {
  let map: MapLibreMap;
  let camera: THREE.Camera;
  let scene: THREE.Scene;
  let renderer: THREE.WebGLRenderer;
  let group: THREE.Group;
  let lastSignature = "";

  const rebuild = () => {
    const state = useGcsStore.getState(); const twin = state.digitalTwin;
    const signature = twin ? `${twin.sequence}:${state.selectedVehicleId}:${state.scaleMode}:${state.viewMode}:${state.digitalTwinLayers.aircraft}` : "empty";
    if (signature === lastSignature) return; lastSignature = signature;
    for (const child of [...group.children]) {
      child.traverse((object) => {
        const mesh = object as THREE.Mesh; mesh.geometry?.dispose();
        (Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []).forEach((material) => material.dispose());
      });
      group.remove(child);
    }
    if (!twin || state.viewMode === "2D" || state.digitalTwinLayers.aircraft === false) return;
    for (const vehicle of twin.vehicles) {
      const coordinate = maplibregl.MercatorCoordinate.fromLngLat(
        [vehicle.position_wgs84.longitude, vehicle.position_wgs84.latitude], vehicle.position_enu_m[2],
      );
      const metres = coordinate.meterInMercatorCoordinateUnits();
      const marker = new THREE.Group(); marker.position.set(coordinate.x, coordinate.y, coordinate.z);
      marker.scale.setScalar(metres * (state.scaleMode === "TRUE_SCALE" ? 1 : 3));
      marker.setRotationFromMatrix(rotationMatrix(bodyQuaternionToThreeRotation(vehicle.quaternion_body_to_enu)));
      const material = new THREE.MeshBasicMaterial({ color: vehicle.vehicle_id === state.selectedVehicleId ? 0x54e7b1 : 0xf8c15c });
      marker.add(new THREE.Mesh(new THREE.BoxGeometry(.4, .24, .14), material));
      const nose = new THREE.Mesh(new THREE.ConeGeometry(.09, .28, 4), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      nose.rotation.z = -Math.PI / 2; nose.position.x = .3; marker.add(nose); group.add(marker);
    }
  };

  return {
    id: "rescueswarm-three-digital-twin", type: "custom", renderingMode: "3d",
    onAdd(mapInstance, gl) {
      map = mapInstance; camera = new THREE.Camera(); scene = new THREE.Scene(); group = new THREE.Group(); scene.add(group);
      renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
      renderer.autoClear = false;
    },
    render(_gl, matrix) {
      rebuild(); camera.projectionMatrix = new THREE.Matrix4().fromArray(matrix as unknown as number[]);
      renderer.resetState(); renderer.render(scene, camera);
    },
    onRemove() {
      for (const child of [...group.children]) {
        child.traverse((object) => {
          const mesh = object as THREE.Mesh; mesh.geometry?.dispose();
          (Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []).forEach((material) => material.dispose());
        });
        group.remove(child);
      }
      renderer.renderLists.dispose();
      renderer.dispose();
    },
  };
}
