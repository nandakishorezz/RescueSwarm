"""Compare held-out pinhole/fisheye validation results, not training RMS alone."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("validations", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("calibration/CALIBRATION_COMPARISON.md"))
    args = parser.parse_args()
    rows = []
    for path in args.validations:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows.append((path.parent.name, value))
    lines = ["# Held-out camera calibration comparison", "", "| Calibration | Images | Mean px | P95 px | Status |", "|---|---:|---:|---:|---|"]
    for name, value in rows:
        lines.append(f"| {name} | {value.get('held_out_images', 0)} | {value.get('mean_reprojection_error_px')} | {value.get('p95_reprojection_error_px')} | {value.get('status')} |")
    measured = [(name, value) for name, value in rows if value.get("p95_reprojection_error_px") is not None]
    winner = min(measured, key=lambda item: item[1]["p95_reprojection_error_px"])[0] if measured else "NOT_AVAILABLE"
    lines.extend(["", f"Lowest held-out P95: **{winner}**", "", "Lens-model selection also requires visual inspection of undistorted held-out previews."])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

