import { useMemo, useState } from "react";

import { cameraRayToWorld, cameraWorldPose, intersectFlatGround, pixelToCameraRay } from "../spatial/camera";
import { quaternionToMatrix } from "../spatial/frames";
import { useGcsStore } from "../stores/gcsStore";

function matrix4(rotation: number[][], translation: number[]): number[][] {
  return [[...rotation[0], translation[0]], [...rotation[1], translation[1]], [...rotation[2], translation[2]], [0, 0, 0, 1]];
}

function format(value: unknown): string { return JSON.stringify(value, null, 2); }

export function SpatialInspector() {
  const twin = useGcsStore((state) => state.digitalTwin);
  const selectedVehicleId = useGcsStore((state) => state.selectedVehicleId);
  const selectedObservationId = useGcsStore((state) => state.selectedObservationId);
  const selectObservation = useGcsStore((state) => state.selectObservation);
  const selectedCoordinate = useGcsStore((state) => state.selectedCoordinate);
  const [pixelX, setPixelX] = useState(640); const [pixelY, setPixelY] = useState(400);
  const [pointA, setPointA] = useState<[number, number] | null>(null); const [pointB, setPointB] = useState<[number, number] | null>(null);
  const [fx, setFx] = useState(800); const [fy, setFy] = useState(800);
  const [mountX, setMountX] = useState(0); const [mountY, setMountY] = useState(0); const [mountZ, setMountZ] = useState(0);
  const vehicle = twin?.vehicles.find((item) => item.vehicle_id === selectedVehicleId) ?? twin?.vehicles[0];
  const calculation = useMemo(() => {
    if (!twin || !vehicle) return null;
    const cameraPose = cameraWorldPose(vehicle.position_enu_m, vehicle.quaternion_body_to_enu, twin.camera.extrinsics);
    const cameraRay = pixelToCameraRay(twin.camera.calibration, [pixelX, pixelY]);
    const worldRay = cameraRayToWorld(cameraPose, cameraRay);
    const intersection = intersectFlatGround(cameraPose.originEnuM, worldRay, twin.ground.elevation_up_m);
    const bodyMatrix = quaternionToMatrix(vehicle.quaternion_body_to_enu);
    const extrinsicsMatrix = quaternionToMatrix(twin.camera.extrinsics.quaternion_camera_to_body);
    const cameraMatrix = quaternionToMatrix(cameraPose.quaternionCameraToEnu);
    return { cameraPose, cameraRay, worldRay, intersection, bodyMatrix, extrinsicsMatrix, cameraMatrix };
  }, [twin, vehicle, pixelX, pixelY]);
  const observation = twin?.observations.find((item) => item.observation_id === selectedObservationId) ?? twin?.observations.at(-1);
  const discrepancy = observation && calculation?.intersection
    ? Math.hypot(...calculation.intersection.pointEnuM.map((value, index) => value - observation.target_enu_m[index])) : null;
  const measurement = pointA && pointB ? (() => {
    const meanLat = (pointA[1] + pointB[1]) / 2 * Math.PI / 180;
    const east = (pointB[0] - pointA[0]) * Math.PI / 180 * 6371008.8 * Math.cos(meanLat);
    const north = (pointB[1] - pointA[1]) * Math.PI / 180 * 6371008.8;
    return { distance: Math.hypot(east, north), bearing: (Math.atan2(east, north) * 180 / Math.PI + 360) % 360 };
  })() : null;
  const applySimulation = () => {
    if (!twin) return;
    useGcsStore.getState().setDigitalTwin({
      ...twin,
      camera: {
        calibration: { ...twin.camera.calibration, camera_matrix: [[fx, 0, twin.camera.calibration.camera_matrix[0][2]], [0, fy, twin.camera.calibration.camera_matrix[1][2]], [0, 0, 1]], evidence_class: "CONFIGURED_ASSUMPTION", quality: "ENGINEERING_SESSION_ONLY" },
        extrinsics: { ...twin.camera.extrinsics, translation_body_m: [mountX, mountY, mountZ], evidence_class: "CONFIGURED_ASSUMPTION", quality: "ENGINEERING_SESSION_ONLY" },
      },
    });
  };
  return <section className="spatial-inspector">
    <div className="panel-title"><h2>SPATIAL INSPECTOR</h2><span className="sim-tag">VISUALIZATION · READ ONLY</span></div>
    {!twin || !vehicle ? <p className="empty">NO VEHICLE DIGITAL-TWIN DATA</p> : <>
      <div className="inspector-grid">
        <span>Vehicle <b>{vehicle.vehicle_id}</b></span><span>Evidence <b>{vehicle.evidence_class}</b></span>
        <span>Mission ENU <b>{vehicle.position_enu_m.map((value) => value.toFixed(2)).join(", ")}</b></span>
        <span>Quaternion [w,x,y,z] <b>{vehicle.quaternion_body_to_enu.map((value) => value.toFixed(4)).join(", ")}</b></span>
        <span>WGS84 <b>{vehicle.position_wgs84.latitude.toFixed(7)}, {vehicle.position_wgs84.longitude.toFixed(7)}</b></span>
        <span>Ground <b>{twin.ground.provider} · {twin.ground.evidence_class}</b></span>
        <span>Camera calibration <b>{twin.camera.calibration.calibration_id} · {twin.camera.calibration.evidence_class}</b></span>
        <span>Camera extrinsics <b>{twin.camera.extrinsics.extrinsics_id} · {twin.camera.extrinsics.evidence_class}</b></span>
        <span>Sensor mode <b>{twin.camera.calibration.sensor_mode} · {twin.camera.calibration.resolution.join(" × ")}</b></span>
        <span>Calibration quality <b>{twin.camera.calibration.quality}</b></span>
      </div>
      <label>Observation<select value={observation?.observation_id ?? ""} onChange={(event) => selectObservation(event.target.value || null)}><option value="">LATEST / NONE</option>{twin.observations.map((item) => <option value={item.observation_id} key={item.observation_id}>{item.frame_id} · {item.pixel_strategy}</option>)}</select></label>
      <div className="inspector-form"><label>Pixel X<input type="number" value={pixelX} onChange={(event) => setPixelX(Number(event.target.value))} /></label><label>Pixel Y<input type="number" value={pixelY} onChange={(event) => setPixelY(Number(event.target.value))} /></label></div>
      <div className="inspector-grid">
        <span>Camera ray <b>{calculation?.cameraRay.map((value) => value.toFixed(5)).join(", ")}</b></span>
        <span>World ray ENU <b>{calculation?.worldRay.map((value) => value.toFixed(5)).join(", ")}</b></span>
        <span>Ground intersection <b>{calculation?.intersection?.pointEnuM.map((value) => value.toFixed(3)).join(", ") ?? "INVALID / ABOVE HORIZON"}</b></span>
        <span>Range / incidence <b>{calculation?.intersection ? `${calculation.intersection.rangeM.toFixed(2)} m / ${calculation.intersection.incidenceAngleDeg.toFixed(1)}°` : "—"}</b></span>
        <span>Backend/frontend discrepancy <b>{discrepancy == null ? "NO MATCHING OBSERVATION" : `${discrepancy.toExponential(2)} m`}</b></span>
      </div>
      <details><summary>TRANSFORM MATRICES</summary><pre>T_world_body\n{format(matrix4(calculation?.bodyMatrix ?? [], vehicle.position_enu_m))}\nT_body_camera\n{format(matrix4(calculation?.extrinsicsMatrix ?? [], twin.camera.extrinsics.translation_body_m))}\nT_world_camera\n{format(matrix4(calculation?.cameraMatrix ?? [], calculation?.cameraPose.originEnuM ?? []))}</pre></details>
      <details><summary>CAMERA / VEHICLE CONFIGURATION SIMULATOR</summary><p>Session-only values never modify physical calibration records.</p><div className="inspector-form">
        <label>fx<input type="number" value={fx} onChange={(event) => setFx(Number(event.target.value))}/></label><label>fy<input type="number" value={fy} onChange={(event) => setFy(Number(event.target.value))}/></label>
        <label>Mount X FRD<input type="number" step="0.01" value={mountX} onChange={(event) => setMountX(Number(event.target.value))}/></label><label>Mount Y FRD<input type="number" step="0.01" value={mountY} onChange={(event) => setMountY(Number(event.target.value))}/></label><label>Mount Z FRD<input type="number" step="0.01" value={mountZ} onChange={(event) => setMountZ(Number(event.target.value))}/></label>
      </div><button onClick={applySimulation}>APPLY SESSION ASSUMPTION</button></details>
      <details><summary>COORDINATE MEASUREMENT</summary><p>Click the map, then capture A and B.</p><div className="control-grid"><button disabled={!selectedCoordinate} onClick={() => setPointA(selectedCoordinate)}>SET A</button><button disabled={!selectedCoordinate} onClick={() => setPointB(selectedCoordinate)}>SET B</button></div>{measurement && <div className="result">HORIZONTAL {measurement.distance.toFixed(2)} m · BEARING {measurement.bearing.toFixed(1)}°</div>}</details>
    </>}
  </section>;
}
