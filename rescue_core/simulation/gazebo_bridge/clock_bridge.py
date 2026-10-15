"""Local clock-only Gazebo subscription and bounded timestamp probe server.

No vehicle pose, image labels, survivor state, or detector data is accessible.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import socket
import threading
import time

from gz.msgs10.clock_pb2 import Clock
from gz.transport13 import Node


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--topic", default="/world/rescueswarm_stage13sim/clock")
    args = parser.parse_args()
    with open(args.pid_file, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    samples: deque[dict[str, int]] = deque(maxlen=256)
    lock = threading.Lock()
    sequence = 0

    def clock_received(message: Clock) -> None:
        nonlocal sequence
        received = time.monotonic_ns()
        if not message.HasField("sim"):
            return
        with lock:
            sequence += 1
            samples.append({
                "sequence": sequence,
                "gazebo_sim_ns": int(message.sim.sec) * 1_000_000_000 + int(message.sim.nsec),
                "bridge_receive_ns": received,
            })

    node = Node()
    if not node.subscribe(Clock, args.topic, clock_received):
        raise RuntimeError("Gazebo clock subscription failed")
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", args.port))
        server.listen(1)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(5)
                try:
                    while connection.recv(1):
                        received = time.monotonic_ns()
                        with lock:
                            batch = list(samples)
                            samples.clear()
                        payload = {
                            "bridge_probe_receive_ns": received,
                            "bridge_probe_send_ns": time.monotonic_ns(),
                            "samples": batch, "clock_topic": args.topic,
                        }
                        connection.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
                except (OSError, TimeoutError):
                    pass


if __name__ == "__main__":
    main()
