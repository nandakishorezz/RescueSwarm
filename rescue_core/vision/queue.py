"""Bounded latest-frame transport and the single authoritative producer."""

from __future__ import annotations

import threading
import time
from collections import deque

from .camera import CameraSource
from .health import ComponentHealth, HealthState
from .models import FramePacket


class LatestFrameQueue:
    def __init__(self, maxsize: int = 2):
        if maxsize < 1:
            raise ValueError("Frame queue size must be at least one")
        self.maxsize = maxsize
        self._frames: deque[FramePacket] = deque()
        self._condition = threading.Condition()
        self.dropped_total = 0
        self.max_observed_depth = 0
        self.closed = False

    def put(self, frame: FramePacket) -> int:
        with self._condition:
            if self.closed:
                raise RuntimeError("Cannot write to a closed frame queue")
            dropped = 0
            while len(self._frames) >= self.maxsize:
                self._frames.popleft()
                dropped += 1
            self.dropped_total += dropped
            frame.dropped_since_previous += dropped
            self._frames.append(frame)
            self.max_observed_depth = max(self.max_observed_depth, len(self._frames))
            self._condition.notify()
            return dropped

    def get_latest(self, timeout_s: float | None = None) -> FramePacket | None:
        with self._condition:
            if not self._frames and not self.closed:
                self._condition.wait(timeout_s)
            if not self._frames:
                return None
            newest = self._frames.pop()
            stale = len(self._frames)
            self._frames.clear()
            if stale:
                self.dropped_total += stale
                newest.dropped_since_previous += stale
            return newest

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()

    def __len__(self) -> int:
        with self._condition:
            return len(self._frames)


class FrameProducer:
    def __init__(
        self, source: CameraSource, output: LatestFrameQueue,
        restart_attempts: int = 3, restart_backoff_s: float = 2,
    ):
        self.source = source
        self.output = output
        self.restart_attempts = restart_attempts
        self.restart_backoff_s = restart_backoff_s
        self.health = ComponentHealth()
        self.frames_captured = 0
        self.capture_errors = 0
        self.sequence_gaps = 0
        self._last_camera_sequence: int | None = None
        self.started_monotonic_ns: int | None = None
        self.last_frame_monotonic_ns: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Frame producer is already running")
        self._stop.clear()
        self.started_monotonic_ns = time.monotonic_ns()
        self._thread = threading.Thread(target=self._run, name="vision-frame-producer", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5) -> None:
        self._stop.set()
        # Interrupt a blocked sensor read before waiting for the producer.
        self.source.stop()
        if self._thread:
            self._thread.join(timeout_s)
        self.output.close()
        if self.running:
            raise RuntimeError("Camera producer did not stop")

    def join(self, timeout_s: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout_s)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.source.start()
                while not self._stop.is_set():
                    frame = self.source.read()
                    if frame is None:
                        self._stop.set()
                        break
                    frame.camera_metadata.extra["vision_enqueue_time_ns"] = time.monotonic_ns()
                    self.output.put(frame)
                    camera_sequence = frame.camera_metadata.extra.get(
                        "libcamera_sequence", frame.camera_metadata.extra.get("source_sequence")
                    )
                    if isinstance(camera_sequence, int) and self._last_camera_sequence is not None:
                        self.sequence_gaps += max(0, camera_sequence - self._last_camera_sequence - 1)
                    if isinstance(camera_sequence, int):
                        self._last_camera_sequence = camera_sequence
                    self.frames_captured += 1
                    self.last_frame_monotonic_ns = frame.capture_timestamp_monotonic_ns
                    self.health.success(self.last_frame_monotonic_ns)
                self.source.stop()
                break
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.capture_errors += 1
                failures += 1
                self.health.error(f"{type(exc).__name__}: {exc}")
                try:
                    self.source.stop()
                except Exception:
                    pass
                if failures > self.restart_attempts or self._stop.is_set():
                    break
                self._stop.wait(self.restart_backoff_s)
        if self.health.last_success_monotonic_ns is None and self.capture_errors:
            self.health.state = HealthState.FAILED
