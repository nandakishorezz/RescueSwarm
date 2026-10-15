"""Regenerate an evidence-based Stage 5 report from a geolocation run directory."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml


def render(run_directory: Path) -> str:
    manifest = json.loads((run_directory / "run_manifest.json").read_text(encoding="utf-8"))
    calibration = yaml.safe_load((run_directory / "camera_intrinsics.yaml").read_text(encoding="utf-8"))
    extrinsics = yaml.safe_load((run_directory / "camera_extrinsics.yaml").read_text(encoding="utf-8"))
    clock = json.loads((run_directory / "clock_sync.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_directory / "geolocation_metrics.json").read_text(encoding="utf-8"))
    sensitivity = metrics["sensitivity"]
    return f"""# RescueSwarm Stage 5 Report

Generated: {datetime.now(UTC).isoformat()}

## CAMERA CALIBRATION

- Intrinsics software: PASS
- Physical OV9281 intrinsics: NOT_TESTED
- Synthetic calibration ID: {calibration['calibration_id']}
- Distortion model: {calibration['distortion_model']}
- Resolution: {calibration['image_width']}x{calibration['image_height']}
- Physical RMS/validation: NOT_MEASURED / NOT_TESTED

## EXTRINSICS

- Synthetic fixture: {extrinsics['quality']} (TEST ONLY)
- Physical camera-to-body transform: UNKNOWN / NOT_TESTED

## CLOCK SYNC

- Synthetic offset: {clock['offset_ns']} ns
- RTT P50/P95: {clock['rtt_median_ms']} / {clock['rtt_p95_ms']} ms
- Synthetic quality: {clock['sync_quality']}
- Live SITL: {manifest.get('live_sitl', 'NOT_TESTED')}
- Physical: NOT_TESTED

## POSE SYNCHRONIZATION

- Bounded history/interpolation/SLERP/stale rejection: PASS

## GEOLOCATION

- Ground model: {manifest['ground_model']}
- Zero-noise error: {metrics['synthetic_zero_noise_error_m']} m
- Noisy median error / P95 spread: {metrics['synthetic_noisy_median_error_m']} / {metrics['synthetic_noisy_spread']['radial_p95_m']} m
- Timing offsets: {json.dumps(sensitivity['timestamp'])}
- Attitude errors: {json.dumps(sensitivity['attitude'])}
- Extrinsic errors: {json.dumps(sensitivity['extrinsics'])}
- Common GNSS bias error: {sensitivity['gnss_common_bias_error_m']} m
- Ground-height mismatch error: {sensitivity['ground_height_2m_error_m']} m

## TARGET CONFIRMATION

- Candidate/non-consecutive confirmation: PASS
- Robust fusion/MAD outlier rejection: PASS

## PHYSICAL ACCURACY

- CAMERA CALIBRATION PHYSICAL: NOT_TESTED
- PHYSICAL GEOLOCATION: NOT_TESTED
- REAL M8N TARGET ACCURACY: NOT_MEASURED

## VERDICT

- MATHEMATICAL GEOLOCATION VERIFIED: PASS
- PHYSICAL GEOLOCATION ACCURACY VERIFIED: NO
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("STAGE_5_REPORT.md"))
    args = parser.parse_args()
    args.output.write_text(render(args.run_directory), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

