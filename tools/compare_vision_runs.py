#!/usr/bin/env python3
"""Compare local benchmark summaries without manually interpreting logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(paths: list[Path]) -> str:
    rows: list[dict[str, object]] = []
    for path in paths:
        summary_path = path / "benchmark_summary.json" if path.is_dir() else path
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        model_path = summary_path.parent / "model.json"
        payload["model_metadata"] = json.loads(model_path.read_text(encoding="utf-8")) if model_path.is_file() else {}
        accuracy_path = summary_path.parent / "accuracy_evaluation.json"
        payload["accuracy_metadata"] = json.loads(accuracy_path.read_text(encoding="utf-8")) if accuracy_path.is_file() else {"status": "NOT_MEASURED"}
        rows.append(payload)
    lines = [
        "# RescueSwarm Vision Benchmark Comparison", "",
        "| Run | Mode | Model | Backend | Input | Capture FPS | Detector FPS | Infer P50/P95 ms | E2E P95 ms | Drops | CPU mean % | RAM max MB | Temp max C | Humans | Accuracy |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        inference, e2e = row.get("inference", {}), row.get("end_to_end", {})
        cpu, ram, temperature = row.get("cpu_percent", {}), row.get("process_rss_bytes", {}), row.get("temperature_c", {})
        model = row.get("model_metadata", {})
        accuracy = row.get("accuracy_metadata", {})
        sweep = accuracy.get("threshold_sweep", []) if accuracy.get("status") == "MEASURED" else []
        best = max(sweep, key=lambda item: item.get("f1", 0), default=None)
        accuracy_text = f"P {best['precision']:.3f} / R {best['recall']:.3f}" if best else "NOT_MEASURED"
        lines.append(
            f"| {row.get('run_id')} | {row.get('mode')} | {model.get('id', 'NONE')} | {model.get('backend', 'NONE')} | {model.get('input_size', 'NONE')} | "
            f"{float(row.get('capture_fps', 0)):.2f} | {float(row.get('detection_output_fps', 0)):.2f} | "
            f"{inference.get('median_ms')} / {inference.get('p95_ms')} | {e2e.get('p95_ms')} | "
            f"{row.get('frames_dropped')} | {cpu.get('mean')} | "
            f"{float(ram.get('max') or 0) / 1024 / 1024:.1f} | {temperature.get('max')} | "
            f"{row.get('human_detections')} | {accuracy_text} |"
        )
    performance_candidates = [
        row for row in rows if row.get("status") == "PASS" and row.get("end_to_end", {}).get("p95_ms") is not None
    ]
    performance_winner = min(
        performance_candidates, key=lambda row: row["end_to_end"]["p95_ms"], default=None
    )
    accuracy_candidates = []
    for row in rows:
        sweep = row.get("accuracy_metadata", {}).get("threshold_sweep", [])
        if sweep:
            accuracy_candidates.append((max(item.get("recall", 0) for item in sweep), row))
    accuracy_winner = max(accuracy_candidates, key=lambda item: item[0], default=None)
    lines.extend([
        "",
        f"Performance winner (latency only): {performance_winner.get('run_id') if performance_winner else 'NOT_AVAILABLE'}",
        f"Accuracy winner: {accuracy_winner[1].get('run_id') if accuracy_winner else 'NOT_AVAILABLE — LABELLED DATA REQUIRED'}",
        "",
        "Performance comparisons do not establish model accuracy. Final selection must consider recall, latency, stability, and resource use.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("VISION_COMPARISON.md"))
    args = parser.parse_args()
    args.output.write_text(compare(args.runs), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
