#!/usr/bin/env bash
set -u

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 CONNECTION [BAUD] [OUTPUT_DIR]" >&2
  echo "Example: $0 /dev/serial0 57600 data/diagnostics/bench_01" >&2
  exit 64
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONNECTION="$1"
BAUD="${2:-57600}"
RUN_DIR="${3:-$REPO_ROOT/data/diagnostics/$(date +%Y-%m-%d_%H-%M-%S)}"

mkdir -p "$RUN_DIR"
cd "$REPO_ROOT"
python3 tools/mavlink_probe.py \
  --connection "$CONNECTION" \
  --baud "$BAUD" \
  --duration 15 \
  --output-dir "$RUN_DIR"
EXIT_CODE=$?
echo "MAVLink evidence saved to: $RUN_DIR"
exit "$EXIT_CODE"

