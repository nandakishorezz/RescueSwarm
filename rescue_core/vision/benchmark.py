"""Capture/inference/end-to-end benchmarking with reproducible local artifacts."""

from __future__ import annotations

import json
import math
import platform
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .camera import CameraSource
from .detectors import DetectorBackend
from .evidence import EvidenceRecorder
from .queue import FrameProducer, LatestFrameQueue
from .system_metrics import ResourceMonitor, ResourceSample, system_snapshot, throttling_detected


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def latency_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "mean_ms": statistics.fmean(values) if values else None,
        "median_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "max_ms": max(values) if values else None,
    }


def numeric_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "mean": statistics.fmean(values) if values else None,
        "median": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


@dataclass(slots=True)
class BenchmarkSummary:
    run_id: str
    mode: Literal["capture_only", "inference_only", "end_to_end"]
    status: str
    duration_s: float
    capture_fps: float
    detector_input_fps: float
    detection_output_fps: float
    frames_captured: int
    frames_inferred: int
    frames_dropped: int
    drop_percent: float
    human_detections: int
    model_load_ms: float | None
    warmup_frames: int
    preprocess: dict[str, float | None]
    inference: dict[str, float | None]
    postprocess: dict[str, float | None]
    end_to_end: dict[str, float | None]
    frame_age_at_inference_start: dict[str, float | None]
    capture_interval_ms: dict[str, float | None]
    cpu_percent: dict[str, float | None]
    process_rss_bytes: dict[str, float | None]
    temperature_c: dict[str, float | None]
    thermal_throttling_detected: bool | None
    memory_growth_bytes: int | None
    camera_errors: int
    detector_errors: int
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VisionRunWriter:
    def __init__(self, root: str | Path, run_id: str | None = None):
        identifier = run_id or uuid4().hex[:8]
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        self.run_directory = Path(root) / f"{stamp}_{identifier}"
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.frames_path = self.run_directory / "frame_stats.ndjson"
        self.detections_path = self.run_directory / "detections.ndjson"

    def json(self, name: str, payload: Any) -> None:
        (self.run_directory / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n", encoding="utf-8"
        )

    def append(self, path: Path, payload: Any) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False) + "\n")

    def finalize(self, summary: BenchmarkSummary) -> None:
        self.json("benchmark_summary.json", summary.as_dict())
        lines = [
            "# RescueSwarm Vision Benchmark", "",
            f"- Run: {summary.run_id}", f"- Mode: {summary.mode}", f"- Status: {summary.status}",
            f"- Duration: {summary.duration_s:.3f} s", f"- Capture FPS: {summary.capture_fps:.3f}",
            f"- Detector FPS: {summary.detection_output_fps:.3f}",
            f"- Frames captured/inferred/dropped: {summary.frames_captured}/{summary.frames_inferred}/{summary.frames_dropped}",
            f"- End-to-end P95: {summary.end_to_end['p95_ms']} ms",
            f"- Thermal throttling: {summary.thermal_throttling_detected if summary.thermal_throttling_detected is not None else 'UNKNOWN'}",
            "- Accuracy: NOT_MEASURED — LABELLED DATASET REQUIRED", "",
        ]
        (self.run_directory / "benchmark_report.md").write_text("\n".join(lines), encoding="utf-8")


class BenchmarkRunner:
    def __init__(
        self, source: CameraSource, detector: DetectorBackend | None, writer: VisionRunWriter,
        queue_size: int = 2, warmup_frames: int = 20, resource_interval_s: float = 1,
        evidence: EvidenceRecorder | None = None,
        restart_attempts: int = 3, restart_backoff_s: float = 2,
    ):
        self.source, self.detector, self.writer = source, detector, writer
        self.queue_size, self.warmup_frames = queue_size, warmup_frames
        self.resource_interval_s = resource_interval_s
        self.evidence = evidence
        self.restart_attempts, self.restart_backoff_s = restart_attempts, restart_backoff_s

    def run(self, duration_s: float, mode: Literal["capture_only", "inference_only", "end_to_end"]) -> BenchmarkSummary:
        if duration_s <= 0:
            raise ValueError("Benchmark duration must be positive")
        if mode != "capture_only" and self.detector is None:
            raise ValueError("Inference benchmarks require a detector")
        return self._run_capture(duration_s) if mode == "capture_only" else self._run_inference(duration_s, mode)

    def _run_capture(self, duration_s: float) -> BenchmarkSummary:
        monitor, samples, intervals, errors = ResourceMonitor(), [], [], []
        frames = 0
        last_capture: int | None = None
        started = time.monotonic()
        next_resource = started
        self.source.start()
        try:
            while time.monotonic() - started < duration_s:
                frame = self.source.read()
                if frame is None:
                    break
                if last_capture is not None:
                    intervals.append((frame.capture_timestamp_monotonic_ns - last_capture) / 1e6)
                last_capture = frame.capture_timestamp_monotonic_ns
                frames += 1
                self.writer.append(self.writer.frames_path, frame.metadata_dict())
                if self.evidence:
                    self.evidence.sample_frame(frame)
                if time.monotonic() >= next_resource:
                    sample = monitor.sample()
                    samples.append(sample)
                    self.writer.append(self.writer.frames_path, {"record_type": "resource", **sample.as_dict()})
                    next_resource += self.resource_interval_s
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            self.source.stop()
        elapsed = max(time.monotonic() - started, 1e-9)
        return self._summary("capture_only", elapsed, frames, 0, 0, [], [], [], [], [], intervals, samples, errors, 0, 0)

    def _run_inference(self, duration_s: float, mode: str) -> BenchmarkSummary:
        detector = self.detector
        assert detector is not None
        detector.load()
        monitor, samples, results, errors = ResourceMonitor(), [], [], []
        queue = LatestFrameQueue(self.queue_size)
        producer = FrameProducer(self.source, queue, self.restart_attempts, self.restart_backoff_s)
        producer.start()
        try:
            warmup_done = 0
            while warmup_done < self.warmup_frames:
                frame = queue.get_latest(timeout_s=2)
                if frame is None:
                    if not producer.running:
                        raise RuntimeError("Camera source ended during detector warm-up")
                    continue
                detector.detect(frame)
                warmup_done += 1
            baseline_captured = producer.frames_captured
            baseline_dropped = queue.dropped_total
            started = time.monotonic()
            next_resource = started
            while time.monotonic() - started < duration_s:
                frame = queue.get_latest(timeout_s=1)
                if frame is None:
                    if not producer.running:
                        break
                    continue
                result = detector.detect(frame)
                results.append(result)
                self.writer.append(self.writer.frames_path, frame.metadata_dict())
                self.writer.append(self.writer.detections_path, result.as_dict())
                if self.evidence:
                    self.evidence.record_detection(frame, result)
                    self.evidence.sample_frame(frame)
                if time.monotonic() >= next_resource:
                    sample = monitor.sample()
                    samples.append(sample)
                    self.writer.append(self.writer.frames_path, {"record_type": "resource", **sample.as_dict()})
                    next_resource += self.resource_interval_s
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            started = locals().get("started", time.monotonic())
            baseline_captured = locals().get("baseline_captured", 0)
            baseline_dropped = locals().get("baseline_dropped", 0)
        finally:
            elapsed = max(time.monotonic() - started, 1e-9)
            producer.stop()
            detector.close()
        captured = max(0, producer.frames_captured - baseline_captured)
        dropped = max(0, queue.dropped_total - baseline_dropped)
        return self._summary(
            mode, elapsed, captured, len(results), dropped,
            [item.preprocess_ms for item in results], [item.inference_ms for item in results],
            [item.postprocess_ms for item in results], [item.end_to_end_ms for item in results],
            [(item.inference_started_ns - item.capture_timestamp_monotonic_ns) / 1e6 for item in results],
            [], samples, errors, sum(len(item.detections) for item in results), producer.capture_errors,
        )

    def _summary(
        self, mode: str, elapsed: float, captured: int, inferred: int, dropped: int,
        preprocess: list[float], inference: list[float], postprocess: list[float], end_to_end: list[float],
        ages: list[float], capture_intervals: list[float], resources: list[ResourceSample],
        errors: list[str], humans: int, camera_errors: int,
    ) -> BenchmarkSummary:
        cpu = [item.cpu_percent for item in resources]
        rss = [float(item.process_rss_bytes) for item in resources]
        temperatures = [item.temperature_c for item in resources if item.temperature_c is not None]
        summary = BenchmarkSummary(
            run_id=self.writer.run_directory.name,
            mode=mode, status="PASS" if not errors else "FAIL", duration_s=elapsed,
            capture_fps=captured / elapsed, detector_input_fps=inferred / elapsed,
            detection_output_fps=inferred / elapsed, frames_captured=captured,
            frames_inferred=inferred, frames_dropped=dropped,
            drop_percent=dropped / captured * 100 if captured else 0,
            human_detections=humans,
            model_load_ms=self.detector.model_load_ms if self.detector else None,
            warmup_frames=self.warmup_frames if self.detector else 0,
            preprocess=latency_summary(preprocess), inference=latency_summary(inference),
            postprocess=latency_summary(postprocess), end_to_end=latency_summary(end_to_end),
            frame_age_at_inference_start=latency_summary(ages),
            capture_interval_ms=latency_summary(capture_intervals),
            cpu_percent=numeric_summary(cpu), process_rss_bytes=numeric_summary(rss),
            temperature_c=numeric_summary(temperatures),
            thermal_throttling_detected=throttling_detected(resources),
            memory_growth_bytes=int(rss[-1] - rss[0]) if len(rss) > 1 else None,
            camera_errors=camera_errors, detector_errors=self.detector.health.errors if self.detector else 0,
            errors=errors,
        )
        self.writer.json("system.json", system_snapshot())
        self.writer.json("camera.json", {
            "source_id": self.source.source_id,
            "source_type": type(self.source).__name__,
            "selected_mode": {
                key: getattr(self.source, key, None) for key in ("width", "height", "fps", "pixel_format", "camera_index")
            },
        })
        self.writer.json("model.json", self.detector.model.model_dump(mode="json") if self.detector else {"status": "NOT_USED"})
        self.writer.json("run_manifest.json", {
            "run_id": summary.run_id, "mode": mode, "created_at": datetime.now().astimezone().isoformat(),
            "python": platform.python_version(), "source": type(self.source).__name__,
            "rescue_swarm_git_commit": _git_commit(),
            "camera_mode": {
                key: getattr(self.source, key, None) for key in ("width", "height", "fps", "pixel_format", "camera_index")
            },
            "model_id": self.detector.model.id if self.detector else None,
            "backend": self.detector.model.backend if self.detector else None,
            "model_sha256": self.detector.model.sha256 if self.detector else None,
            "model_input_size": self.detector.model.input_size if self.detector else None,
            "confidence_threshold": self.detector.confidence_threshold if self.detector else None,
            "duration_s": summary.duration_s,
            "warmup_frames": summary.warmup_frames,
            "queue_size": self.queue_size,
            "accuracy": "NOT_MEASURED — LABELLED DATASET REQUIRED",
        })
        self.writer.finalize(summary)
        return summary


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=3, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    return completed.stdout.strip() if completed.returncode == 0 else "UNKNOWN"
