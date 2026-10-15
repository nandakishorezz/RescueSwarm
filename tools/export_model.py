#!/usr/bin/env python3
"""Development-only export of an explicitly local model; never downloads weights."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.vision.manifest import artifact_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing local Ultralytics-format model file")
    parser.add_argument("--format", choices=("onnx", "ncnn"), required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("Source model must already exist locally; automatic downloads are forbidden")
    try:
        from ultralytics import YOLO
    except ImportError:
        print("Export tooling requires a separate development environment with Ultralytics installed.", file=sys.stderr)
        return 1
    exported = Path(YOLO(str(args.source)).export(format=args.format, imgsz=args.imgsz))
    print(f"Exported: {exported}")
    print(f"SHA-256: {artifact_sha256(exported)}")
    print("Copy the artifact under models/artifacts and update models/manifest.yaml explicitly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
