"""Create deterministic Stage 11S spatial/UI qualification evidence.

This is a software-only check. It never opens MAVLink and never touches hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
CRITICAL_FILES = (
    "ground/frontend/src/spatial/frames.ts",
    "ground/frontend/src/spatial/camera.ts",
    "ground/frontend/src/spatial/replay.ts",
    "ground/frontend/src/components/DigitalTwinView.tsx",
    "ground/frontend/src/components/digitalTwinLayer.ts",
    "ground/backend/app/api/digital_twin.py",
    "data/fixtures/stage11s_spatial_geometry.json",
    "config/ground.yaml",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quat_multiply(a: Iterable[float], b: Iterable[float]) -> list[float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]


def _rotate(q: Iterable[float], v: Iterable[float]) -> list[float]:
    values = list(q)
    magnitude = math.sqrt(sum(value * value for value in values))
    unit = [value / magnitude for value in values]
    rotated = _quat_multiply(_quat_multiply(unit, [0.0, *v]), [unit[0], -unit[1], -unit[2], -unit[3]])
    return rotated[1:]


def _fixture_result() -> dict[str, object]:
    fixture = json.loads((ROOT / "data/fixtures/stage11s_spatial_geometry.json").read_text(encoding="utf-8"))
    calibration = fixture["calibration"]
    matrix = calibration["camera_matrix"]
    pixel = fixture["pixel"]
    camera_ray = [(pixel[0] - matrix[0][2]) / matrix[0][0], (pixel[1] - matrix[1][2]) / matrix[1][1], 1.0]
    norm = math.sqrt(sum(value * value for value in camera_ray))
    camera_ray = [value / norm for value in camera_ray]
    body_ray = _rotate(fixture["extrinsics"]["quaternion_camera_to_body"], camera_ray)
    world_ray = _rotate(fixture["quaternion_body_to_enu"], body_ray)
    origin = fixture["vehicle_position_enu_m"]
    scale = (fixture["ground_up_m"] - origin[2]) / world_ray[2]
    hit = [origin[index] + scale * world_ray[index] for index in range(3)]
    expected = fixture["expected_intersection_enu_m"]
    discrepancy = math.sqrt(sum((hit[index] - expected[index]) ** 2 for index in range(3)))
    return {
        "evidence_class": fixture["evidence_class"],
        "computed_camera_ray": camera_ray,
        "computed_world_ray_enu": world_ray,
        "computed_intersection_enu_m": hit,
        "expected_intersection_enu_m": expected,
        "maximum_discrepancy_m": discrepancy,
        "pass": discrepancy <= 1e-9,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/stage11s_runs")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    run_id = f"stage11s_{now.strftime('%Y%m%dT%H%M%SZ')}"
    output = args.output_root / run_id
    output.mkdir(parents=True, exist_ok=False)

    stage10_manifest = ROOT / "SOFTWARE_RELEASE_MANIFEST.json"
    if not stage10_manifest.exists():
        candidates = sorted((ROOT / "release").glob("stage10s_rc_*/SOFTWARE_RELEASE_MANIFEST.json"))
        stage10_manifest = candidates[-1] if candidates else stage10_manifest

    source_hashes = {relative: _sha256(ROOT / relative) for relative in CRITICAL_FILES}
    fixture = _fixture_result()
    result = {
        "stage": "11S",
        "run_id": run_id,
        "created_at": now.isoformat(),
        "execution_source": "SOFTWARE_ONLY",
        "physical_hardware_touched": False,
        "hardware_gate_h1": "PENDING",
        "stage10_baseline": {
            "path": str(stage10_manifest.relative_to(ROOT)) if stage10_manifest.exists() else None,
            "sha256": _sha256(stage10_manifest) if stage10_manifest.exists() else None,
            "modified": False,
        },
        "spatial_fixture": fixture,
        "source_hashes": source_hashes,
        "offline_assets": {
            "three_bundled_locally": (ROOT / "ground/frontend/node_modules/three").exists(),
            "maplibre_bundled_locally": (ROOT / "ground/frontend/node_modules/maplibre-gl").exists(),
            "cdn_required": False,
        },
        "physical_values": {
            "vehicle_dimensions": "CONFIGURED_ASSUMPTION",
            "camera_intrinsics": "PHYSICAL_UNVERIFIED",
            "camera_extrinsics": "PHYSICAL_UNVERIFIED",
            "real_camera_fov": "PHYSICAL_UNVERIFIED",
        },
    }
    (output / "stage11s_qualification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = f"""# Stage 11S qualification evidence

- Run: `{run_id}`
- Execution source: SOFTWARE_ONLY
- Physical hardware touched: NO
- Hardware Gate H1: **PENDING**
- Shared spatial fixture: **{'PASS' if fixture['pass'] else 'FAIL'}**
- Maximum fixture discrepancy: `{fixture['maximum_discrepancy_m']:.3e} m`
- Stage 10 baseline manifest changed: NO
- CDN required: NO

This report contains software/digital-twin evidence only. It is not physical camera, vehicle, video, or flight evidence.
"""
    (output / "stage11s_qualification_report.md").write_text(report, encoding="utf-8")
    print(output)
    return 0 if fixture["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
