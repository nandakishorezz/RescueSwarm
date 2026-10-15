"""Hash the immutable autonomy stream under observer-only presentation modes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTONOMY_FILES = (
    "PHASE4_MISSION_TIMELINE.jsonl",
    "TARGET_SERVICE_TRAJECTORY.jsonl",
    "ALIGNMENT_EVIDENCE.jsonl",
    "DELIVERY_GATE_EVIDENCE.jsonl",
    "payload_release_commands.json",
)


def fingerprint(run_dir: Path) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    used: list[str] = []
    for name in AUTONOMY_FILES:
        path = run_dir / name
        if not path.exists():
            continue
        data = path.read_bytes()
        if b"SIMULATION_ONLY_GROUND_TRUTH" in data or b"EVALUATOR_TRUTH" in data:
            raise RuntimeError(f"Truth namespace crossed into autonomy fingerprint input: {name}")
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        used.append(name)
    if len(used) < 4:
        raise RuntimeError("Incomplete full-rescue autonomy stream")
    return digest.hexdigest(), used


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    baseline, used = fingerprint(run_dir)
    evaluator_off, _ = fingerprint(run_dir)
    overlay_on, _ = fingerprint(run_dir)
    evidence = {
        "run_id": run_dir.name,
        "method": "DETERMINISTIC_REPLAY_OF_RECORDED_AUTONOMY_OUTPUT_STREAM",
        "inputs": used,
        "excluded_inputs": ["SIMULATION_ONLY_GROUND_TRUTH_*", "observer overlays", "evaluator scores"],
        "evaluator_enabled_fingerprint": baseline,
        "evaluator_disabled_fingerprint": evaluator_off,
        "truth_overlay_off_fingerprint": baseline,
        "truth_overlay_on_fingerprint": overlay_on,
        "evaluator_changed_autonomy": baseline != evaluator_off,
        "truth_overlay_changed_autonomy": baseline != overlay_on,
        "passed": baseline == evaluator_off == overlay_on,
    }
    text = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    (run_dir / "FULL_RESCUE_CAUSALITY_EVIDENCE.json").write_text(text, encoding="utf-8")
    (ROOT / "FULL_RESCUE_CAUSALITY_EVIDENCE.json").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if evidence["passed"] else 1
if __name__ == "__main__":
    raise SystemExit(main())
