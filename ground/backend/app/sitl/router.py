"""Single authoritative MAVLink reader with typed internal dispatch queues."""

from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any


class MAVLinkMessageRouter:
    """The only object permitted to call recv_match on a live SITL connection."""

    def __init__(self, connection: Any, poll_timeout_s: float = 0.2, queue_size: int = 1000):
        if queue_size < 1:
            raise ValueError("MAVLink router queue_size must be positive")
        self.connection = connection
        self.poll_timeout_s = poll_timeout_s
        self._queues: dict[str, queue.Queue[Any]] = defaultdict(lambda: queue.Queue(maxsize=queue_size))
        self._latest: dict[str, Any] = {}
        self._callbacks: list[Callable[[Any], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_message_monotonic: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive_loop, name="sitl-mavlink-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_s)
        self._thread = None

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        self._callbacks.append(callback)

    def latest(self, message_type: str) -> Any | None:
        return self._latest.get(message_type)

    def wait_for(
        self,
        message_types: str | tuple[str, ...],
        timeout_s: float,
        predicate: Callable[[Any], bool] | None = None,
    ) -> Any | None:
        types = (message_types,) if isinstance(message_types, str) else message_types
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for message_type in types:
                try:
                    message = self._queues[message_type].get_nowait()
                except queue.Empty:
                    continue
                if predicate is None or predicate(message):
                    return message
            time.sleep(0.005)
        return None

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                message = self.connection.recv_match(blocking=True, timeout=self.poll_timeout_s)
            except Exception:
                if self._stop.is_set():
                    break
                time.sleep(self.poll_timeout_s)
                continue
            if message is None:
                continue
            message_type = message.get_type()
            if message_type == "BAD_DATA":
                continue
            self.last_message_monotonic = time.monotonic()
            self._latest[message_type] = message
            destination = self._queues[message_type]
            try:
                destination.put_nowait(message)
            except queue.Full:
                # UI/diagnostic consumers need current evidence, not an unbounded stale backlog.
                try:
                    destination.get_nowait()
                except queue.Empty:
                    pass
                destination.put_nowait(message)
            for callback in tuple(self._callbacks):
                try:
                    callback(message)
                except Exception:
                    pass
