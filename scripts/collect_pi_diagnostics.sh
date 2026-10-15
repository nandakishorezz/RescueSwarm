#!/usr/bin/env bash
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${1:-$REPO_ROOT/data/diagnostics/$(date +%Y-%m-%d_%H-%M-%S)}"
CONNECTION="${MAVLINK_CONNECTION:-/dev/serial0}"

mkdir -p "$RUN_DIR"
cd "$REPO_ROOT"
python3 tools/system_info.py --output-dir "$RUN_DIR"
python3 tools/detect_serial_ports.py --connection "$CONNECTION" --output-dir "$RUN_DIR"
echo "Pi and serial evidence saved to: $RUN_DIR"

