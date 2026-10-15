"""Bounded asynchronous observation work queue; never blocks the vision producer."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


class GeolocationWorker:
    def __init__(self, handler: Callable[[Any], None], queue_size: int = 32):
        self.handler = handler
        self.queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self.dropped = 0
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, item: Any) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
        self.queue.put_nowait(item)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="geolocation-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_s)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.handler(item)
            except Exception as exc:  # health evidence is retained; worker survives bounded failures.
                self.errors.append(f"{type(exc).__name__}: {exc}")

