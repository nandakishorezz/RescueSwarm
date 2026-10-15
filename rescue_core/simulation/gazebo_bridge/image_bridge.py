"""Bounded Gazebo Transport image subscriber with a loopback binary stream.

This process runs inside the Linux environment that owns Gazebo.  It performs no
inference and has no access to simulation ground truth.  The Gazebo callback
copies one image into a small drop-oldest deque and returns immediately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from gz.msgs10.image_pb2 import Image
from gz.transport13 import Node


@dataclass(slots=True)
class BufferedImage:
    header: dict[str, object]
    pixels: bytes


class DropOldestImages:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.items: deque[BufferedImage] = deque()
        self.condition = threading.Condition()
        self.generated = 0
        self.dropped = 0
        self.duplicates = 0
        self._previous_checksum: str | None = None

    def put(self, message: Image) -> None:
        received_ns = time.monotonic_ns()
        if not message.HasField("header") or not message.header.HasField("stamp"):
            return  # Missing capture authority must never become receive time.
        pixels = bytes(message.data)
        checksum = hashlib.sha256(pixels).hexdigest()
        raw = np.frombuffer(pixels, dtype=np.uint8)
        stamp = message.header.stamp
        sim_time_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nsec)
        with self.condition:
            self.generated += 1
            sequence = self.generated - 1
            if checksum == self._previous_checksum:
                self.duplicates += 1
            self._previous_checksum = checksum
            if len(self.items) >= self.capacity:
                self.items.popleft()
                self.dropped += 1
            self.items.append(BufferedImage(
                header={
                    "source_sequence": sequence,
                    "sim_capture_time_ns": sim_time_ns,
                    "capture_timestamp_source": "gz.msgs.Image.header.stamp",
                    "capture_stamp_sec": int(stamp.sec),
                    "capture_stamp_nsec": int(stamp.nsec),
                    "bridge_receive_time_ns": received_ns,
                    "width": int(message.width),
                    "height": int(message.height),
                    "step": int(message.step),
                    "pixel_format_type": int(message.pixel_format_type),
                    "data_length": len(pixels),
                    "checksum_sha256": checksum,
                    "raw_min": int(raw.min()) if raw.size else None,
                    "raw_max": int(raw.max()) if raw.size else None,
                    "raw_mean": float(raw.mean()) if raw.size else None,
                    "raw_std": float(raw.std()) if raw.size else None,
                    "frames_generated": self.generated,
                    "frames_dropped_bridge": self.dropped,
                    "duplicate_frames": self.duplicates,
                },
                pixels=pixels,
            ))
            self.condition.notify()

    def get(self, timeout_s: float = 1.0) -> BufferedImage | None:
        with self.condition:
            if not self.items:
                self.condition.wait(timeout_s)
            if not self.items:
                return None
            newest = self.items.pop()
            skipped = len(self.items)
            if skipped:
                self.dropped += skipped
                self.items.clear()
                newest.header["frames_dropped_bridge"] = self.dropped
            return newest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--capacity", type=int, default=3, choices=range(2, 5))
    parser.add_argument("--pid-file", required=True)
    return parser.parse_args()


def send_image(client: socket.socket, image: BufferedImage) -> None:
    header = json.dumps(image.header, separators=(",", ":")).encode("utf-8")
    client.sendall(struct.pack("!I", len(header)))
    client.sendall(header)
    client.sendall(image.pixels)


def main() -> int:
    args = parse_args()
    os.makedirs(os.path.dirname(args.pid_file), exist_ok=True)
    with open(args.pid_file, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))

    queue = DropOldestImages(args.capacity)
    node = Node()
    if not node.subscribe(Image, args.topic, queue.put):
        raise RuntimeError(f"Could not subscribe to Gazebo image topic: {args.topic}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", args.port))
        server.listen(1)
        server.settimeout(1.0)
        while True:
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            with client:
                client.settimeout(60.0)
                while True:
                    image = queue.get()
                    if image is None:
                        continue
                    try:
                        send_image(client, image)
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        break


if __name__ == "__main__":
    raise SystemExit(main())
