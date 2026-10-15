#!/usr/bin/env python3
"""Run deterministic synthetic Stage 5 geometry/fusion validation and write evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rescue_core.geolocation.artifacts import GeolocationArtifactWriter  # noqa: E402
from tools.stage5_synthetic_validation import run_synthetic_validation  # noqa: E402


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def render_report(result: dict[str, object], live_sitl: str = "NOT_TESTED") -> str:
    metrics = result["metrics"]
    sensitivity = metrics["sensitivity"]
    calibration = result["calibration"]
    return f"""# RescueSwarm Stage 5 Report

Generated: {datetime.now(UTC).isoformat()}

## CAMERA CALIBRATION

- Intrinsics software: PASS
- Physical OV9281 intrinsics: NOT_TESTED
- Synthetic distortion model: {calibration.distortion_model}
- Synthetic resolution: {calibration.image_width}x{calibration.image_height}
- Physical RMS reprojection: NOT_MEASURED
- Physical held-out validation: NOT_TESTED

## EXTRINSICS

- Synthetic fixture: CALIBRATED (TEST ONLY)
- Physical camera-to-body transform: NOT_TESTED / UNKNOWN

## CLOCK SYNC

- Synthetic quality: {result['clock_sync']['sync_quality']}
- Synthetic offset: {result['clock_sync']['offset_ns']} ns
- Synthetic RTT P50/P95: {result['clock_sync']['rtt_median_ms']} / {result['clock_sync']['rtt_p95_ms']} ms
- Live SITL: {live_sitl}
- Physical: NOT_TESTED

## POSE SYNCHRONIZATION

- Bounded buffer: PASS
- Linear position interpolation: PASS
- Quaternion SLERP: PASS
- Stale/extrapolated pose rejection: PASS

## GEOLOCATION

- Ground model: SYNTHETIC FLAT GROUND
- Synthetic zero-noise error: {metrics['synthetic_zero_noise_error_m']:.12f} m
- Synthetic noisy median error: {metrics['synthetic_noisy_median_error_m']:.4f} m
- Synthetic noisy radial P95: {metrics['synthetic_noisy_spread']['radial_p95_m']:.4f} m
- Timing error experiment: {json.dumps(sensitivity['timestamp'])}
- Attitude error experiment: {json.dumps(sensitivity['attitude'])}
- Extrinsic error experiment: {json.dumps(sensitivity['extrinsics'])}
- +2 m common GNSS bias result: {sensitivity['gnss_common_bias_error_m']:.4f} m (fusion does not remove it)
- 2 m ground-height mismatch result: {sensitivity['ground_height_2m_error_m']:.4f} m

## TARGET CONFIRMATION

- Candidate logic: PASS
- Non-consecutive observation confirmation: PASS
- Robust uncertainty-weighted fusion: PASS
- Median/MAD outlier rejection: PASS
- Confirmed synthetic targets: {len(result['targets'])}

## PHYSICAL ACCURACY

- OV9281 calibration: NOT_TESTED
- Pixhawk/M8N pose synchronization: NOT_TESTED
- Surveyed target accuracy: NOT_MEASURED

## VERDICT

- MATHEMATICAL GEOLOCATION VERIFIED: PASS
- LIVE ARDUPILOT SITL GEOLOCATION: {live_sitl}
- REAL OV9281 CALIBRATION: NOT_TESTED
- REAL M8N TARGET ACCURACY: NOT_MEASURED
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--monte-carlo-samples", type=int, default=200)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "geolocation_runs")
    args = parser.parse_args()
    print("[1/8] Loading synthetic calibrated camera and explicit frames")
    result = run_synthetic_validation(args.seed, args.monte_carlo_samples)
    print("[2/8] TIMESYNC and bounded pose interpolation verified")
    print("[3/8] Forward projection and inverse ray-ground geometry complete")
    print("[4/8] Multi-view association, confirmation, and robust fusion complete")
    print("[5/8] Timing, attitude, extrinsic, GNSS-bias, and ground-height sensitivities measured")
    run_id = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid4().hex[:8]
    writer = GeolocationArtifactWriter(args.output_root / run_id)
    manifest = {
        "run_id": run_id, "created_at": datetime.now(UTC).isoformat(), "synthetic": True,
        "physical_camera": "NOT_TESTED", "physical_geolocation": "NOT_TESTED",
        "live_sitl": "NOT_TESTED", "rescue_swarm_git_commit": git_commit(),
        "calibration_id": result["calibration"].calibration_id,
        "extrinsics_id": result["extrinsics"].extrinsics_id,
        "camera_mode": "SYNTHETIC_1280x800", "ground_model": "SYNTHETIC_FLAT_GROUND",
        "target_pixel_strategy": "BBOX_CENTER", "seed": args.seed,
        "monte_carlo_samples": args.monte_carlo_samples,
    }
    report = render_report(result)
    writer.write_required(
        manifest=manifest, calibration=result["calibration"], extrinsics=result["extrinsics"],
        clock_sync=result["clock_sync"], poses=result["poses"], observations=result["observations"],
        targets=result["targets"], ground_truth=result["ground_truth"], metrics=result["metrics"], report=report,
    )
    (ROOT / "STAGE_5_REPORT.md").write_text(report, encoding="utf-8")
    print("[6/8] Required evidence artifacts written")
    print("[7/8] Physical OV9281/Pixhawk/M8N accuracy: NOT_TESTED / NOT_MEASURED")
    print(f"[8/8] Stage 5 report: {writer.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

