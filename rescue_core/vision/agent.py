"""Independent airborne vision process boundary; it never controls flight."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .camera import CameraSource
from .detectors import DetectorBackend, DetectorError
from .events import VisionEvent
from .evidence import EvidenceRecorder
from .health import ComponentHealth, HealthState
from .models import DetectionResult, FramePacket
from .queue import FrameProducer, LatestFrameQueue
from .benchmark import percentile
from .system_metrics import ResourceMonitor, ResourceSample


class VisionAgent:
    def __init__(
        self, source: CameraSource, detector: DetectorBackend,
        queue_size: int = 2, no_frame_timeout_s: float = 2,
        evidence: EvidenceRecorder | None = None,
        event_callback: Callable[[VisionEvent], None] | None = None,
        status_path: str | Path | None = None,
        restart_attempts: int = 3,
        restart_backoff_s: float = 2,
        *,
        result_callback: Callable[[FramePacket, DetectionResult], None] | None = None,
    ):
        self.source, self.detector = source, detector
        self.queue = LatestFrameQueue(queue_size)
        self.producer = FrameProducer(source, self.queue, restart_attempts, restart_backoff_s)
        self.no_frame_timeout_s = no_frame_timeout_s
        self.evidence = evidence
        self.event_callback = event_callback or (lambda _: None)
        self.result_callback = result_callback or (lambda _frame, _result: None)
        self.status_path = Path(status_path) if status_path else None
        self.detector_health = ComponentHealth()
        self.results = 0
        self.human_detections = 0
        self.started_monotonic_ns: int | None = None
        self.latencies_ms: deque[float] = deque(maxlen=500)
        self.resource_monitor = ResourceMonitor()
        self.latest_resource: ResourceSample | None = None
        self.next_resource_monotonic = 0.0
        self._stop = threading.Event()
        self.status_write_failures = 0

    def run(self) -> None:
        self._stop.clear()
        self.started_monotonic_ns = time.monotonic_ns()
        self.next_resource_monotonic = time.monotonic()
        try:
            self.detector.load()
            self.detector_health.success()
        except Exception as exc:
            self.detector_health.error(f"{type(exc).__name__}: {exc}", degraded_until=1)
            self._publish_health()
            raise
        self.producer.start()
        self._publish_health()
        try:
            while not self._stop.is_set():
                frame = self.queue.get_latest(timeout_s=self.no_frame_timeout_s)
                if frame is None:
                    state = self.producer.health.evaluate_timeout(self.no_frame_timeout_s)
                    self._publish_health()
                    if state is HealthState.FAILED and not self.producer.running:
                        break
                    continue
                try:
                    result = self.detector.detect(frame)
                except DetectorError as exc:
                    self.detector_health.error(str(exc))
                    self._publish_health()
                    continue
                self.detector_health.success(result.inference_finished_ns)
                self.results += 1
                self.latencies_ms.append(result.end_to_end_ms)
                self.human_detections += len(result.detections)
                if time.monotonic() >= self.next_resource_monotonic:
                    self.latest_resource = self.resource_monitor.sample()
                    self.next_resource_monotonic += 1
                if self.evidence:
                    self.evidence.record_detection(frame, result)
                    self.evidence.sample_frame(frame)
                self.result_callback(frame, result)
                self.event_callback(VisionEvent.create(
                    "vision.detection", frame.source_id, result.as_dict()
                ))
                self.event_callback(VisionEvent.create(
                    "vision.frame_stats", frame.source_id,
                    {
                        "frame_id": frame.frame_id,
                        "sequence": frame.sequence,
                        "queue_dropped_total": self.queue.dropped_total,
                        "end_to_end_ms": result.end_to_end_ms,
                    },
                ))
                self._write_status(result.end_to_end_ms)
        finally:
            self.producer.stop()
            self.detector.close()
            self._publish_health()

    def stop(self) -> None:
        self._stop.set()

    def _publish_health(self) -> None:
        payload = self.status()
        self.event_callback(VisionEvent.create("vision.health", self.source.source_id, payload))
        self._write_status(None)

    def status(self) -> dict[str, object]:
        elapsed_s = (
            (time.monotonic_ns() - self.started_monotonic_ns) / 1e9
            if self.started_monotonic_ns else 0
        )
        return {
            "connected": self.producer.running,
            "source": type(self.source).__name__,
            "camera_health": self.producer.health.as_dict(),
            "detector_health": self.detector_health.as_dict(),
            "frames_captured": self.producer.frames_captured,
            "camera_sequence_gaps": self.producer.sequence_gaps,
            "frames_processed": self.results,
            "capture_fps": self.producer.frames_captured / elapsed_s if elapsed_s else 0,
            "detector_fps": self.results / elapsed_s if elapsed_s else 0,
            "p95_latency_ms": percentile(list(self.latencies_ms), 95),
            "dropped_frames": self.queue.dropped_total,
            "human_detections": self.human_detections,
            "status_write_failures": self.status_write_failures,
            "cpu_percent": self.latest_resource.cpu_percent if self.latest_resource else None,
            "ram_rss_bytes": self.latest_resource.process_rss_bytes if self.latest_resource else None,
            "temperature_c": self.latest_resource.temperature_c if self.latest_resource else None,
            "throttling": self.latest_resource.throttling if self.latest_resource else None,
            "physical_camera_verified": (
                type(self.source).__name__ == "Picamera2CameraSource"
                and self.producer.frames_captured > 0
                and self.producer.health.state is HealthState.READY
            ),
            "source_metrics": (
                self.source.metrics.as_dict()
                if hasattr(self.source, "metrics") and hasattr(self.source.metrics, "as_dict") else None
            ),
        }

    def _write_status(self, latest_latency_ms: float | None) -> None:
        if not self.status_path:
            return
        payload = {**self.status(), "latest_end_to_end_ms": latest_latency_ms, "updated_wall_ns": time.time_ns()}
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        try:
            temporary.replace(self.status_path)
        except PermissionError:
            # A Windows UI reader can briefly lock the destination. Keep the
            # previous snapshot and retry on the next frame, not in inference.
            self.status_write_failures += 1
