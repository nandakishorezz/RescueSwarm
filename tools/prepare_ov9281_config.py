#!/usr/bin/env python3
"""Safely inspect and optionally apply a minimal OV9281 boot overlay configuration."""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class CameraConfigInspection:
    auto_detect_lines: list[str]
    overlay_lines: list[str]
    ov9281_lines: list[str]
    legacy_lines: list[str]
    duplicate_ov9281: bool
    conflicting_camera_overlays: list[str]


def is_raspberry_pi() -> bool:
    try:
        return "Raspberry Pi" in Path("/proc/device-tree/model").read_text(encoding="utf-8")
    except OSError:
        return False


def inspect_config(text: str) -> CameraConfigInspection:
    active = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    auto = [line for line in active if line.partition("=")[0].strip() == "camera_auto_detect"]
    overlays = [line for line in active if line.partition("=")[0].strip() == "dtoverlay"]
    ov9281 = [line for line in overlays if "ov9281" in line.casefold()]
    camera_terms = ("ov5647", "imx", "ov64", "ov2311", "camera-mux")
    conflicts = [line for line in overlays if "ov9281" not in line.casefold() and any(term in line.casefold() for term in camera_terms)]
    legacy = [line for line in active if any(term in line.casefold() for term in ("start_x", "gpu_mem", "bcm2835-v4l2"))]
    return CameraConfigInspection(auto, overlays, ov9281, legacy, len(ov9281) > 1, conflicts)


def proposed_config(text: str, connector: str) -> tuple[str, list[str]]:
    inspection = inspect_config(text)
    blockers: list[str] = []
    if inspection.duplicate_ov9281:
        blockers.append("Multiple active OV9281 overlays exist; resolve connector intent manually")
    if len(inspection.auto_detect_lines) > 1:
        blockers.append("Multiple active camera_auto_detect settings exist")
    if inspection.conflicting_camera_overlays:
        blockers.append("Other active camera sensor overlays require manual review")
    lines = text.splitlines()
    auto_indices = [index for index, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#") and line.partition("=")[0].strip() == "camera_auto_detect"]
    if auto_indices:
        lines[auto_indices[0]] = "camera_auto_detect=0"
    else:
        lines.append("camera_auto_detect=0")
    desired = "dtoverlay=ov9281" + (f",{connector}" if connector in {"cam0", "cam1"} else "")
    if not inspection.ov9281_lines:
        lines.append(desired)
    elif inspection.ov9281_lines[0] != desired:
        blockers.append(f"Existing OV9281 overlay differs from requested connector: {inspection.ov9281_lines[0]}")
    return "\n".join(lines) + "\n", blockers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="Inspect and display the proposal without writing (default)")
    action.add_argument("--apply", action="store_true", help="Create a backup and apply the displayed change")
    parser.add_argument("--connector", choices=("auto", "cam0", "cam1"), default="auto")
    parser.add_argument("--config", type=Path, default=Path("/boot/firmware/config.txt"))
    args = parser.parse_args()
    if not is_raspberry_pi():
        print("NOT_TESTED: this helper only operates on a Raspberry Pi", file=sys.stderr)
        return 0
    if not args.config.is_file():
        print(f"FAILED: boot configuration not found: {args.config}", file=sys.stderr)
        return 1
    original = args.config.read_text(encoding="utf-8")
    inspection = inspect_config(original)
    proposal, blockers = proposed_config(original, args.connector)
    print(f"Pi configuration: {args.config}")
    print(f"camera_auto_detect: {inspection.auto_detect_lines or ['NOT SET']}")
    print(f"camera overlays: {inspection.overlay_lines or ['NONE']}")
    print(f"legacy/non-standard options: {inspection.legacy_lines or ['NONE']}")
    if args.connector == "auto":
        print("Connector: unspecified. On a multi-CSI Pi, confirm CAM0/CAM1 cabling before applying.")
    print("\n".join(difflib.unified_diff(
        original.splitlines(), proposal.splitlines(), fromfile=str(args.config), tofile=str(args.config) + ".proposed", lineterm=""
    )) or "No change required")
    if blockers:
        print("APPLY BLOCKED:")
        for blocker in blockers:
            print(f"- {blocker}")
        return 1 if args.apply else 0
    if not args.apply:
        print("DRY RUN ONLY: no file was modified. Re-run with --apply after confirming the CSI connector.")
        return 0
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    backup = args.config.with_name(f"{args.config.name}.backup.{stamp}")
    shutil.copy2(args.config, backup)
    args.config.write_text(proposal, encoding="utf-8")
    print(f"Applied. Backup: {backup}")
    print("Reboot was NOT performed. Review both files, then reboot manually if correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
