#!/usr/bin/env python3
"""Generate the evidence-backed Stage 6 report from one hardware run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rescue_core.hardware.report import render_stage6_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("STAGE_6_REPORT.md"))
    args = parser.parse_args()
    args.output.write_text(render_stage6_report(args.run_directory), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
