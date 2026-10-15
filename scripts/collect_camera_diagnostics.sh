#!/usr/bin/env bash
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${1:-$REPO_ROOT/data/diagnostics/$(date +%Y-%m-%d_%H-%M-%S)}"

mkdir -p "$RUN_DIR"
cd "$REPO_ROOT"
python3 tools/camera_probe.py --output-dir "$RUN_DIR"
echo "Camera evidence saved to: $RUN_DIR"

