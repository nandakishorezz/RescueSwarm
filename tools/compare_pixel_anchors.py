#!/usr/bin/env python3
"""Compare measured BBOX_CENTER and BBOX_BOTTOM_CENTER static geolocation errors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("center", type=Path)
    parser.add_argument("bottom_center", type=Path)
    parser.add_argument("--output", type=Path, default=Path("anchor_comparison_report.md"))
    args = parser.parse_args()
    center = json.loads(args.center.read_text(encoding="utf-8"))
    bottom = json.loads(args.bottom_center.read_text(encoding="utf-8"))
    def metric(value: dict, key: str):
        metrics = value.get("metrics", value)
        return (metrics.get("radial_error_m") or {}).get(key, "NOT_MEASURED")
    lines = [
        "# Physical pixel-anchor comparison", "",
        "| Strategy | P50 radial error (m) | P95 radial error (m) | Max (m) |",
        "|---|---:|---:|---:|",
        f"| BBOX_CENTER | {metric(center, 'p50')} | {metric(center, 'p95')} | {metric(center, 'maximum')} |",
        f"| BBOX_BOTTOM_CENTER | {metric(bottom, 'p50')} | {metric(bottom, 'p95')} | {metric(bottom, 'maximum')} |",
        "", "Results apply only to the recorded subject pose and fixture. Standing-person evidence does not settle the prone-person strategy.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
