"""Bounded relay for the SIM_JSON clock witness datagrams.

Runs inside the Linux environment that owns ArduPilot SITL.  The patched
SIM_JSON::recv_fdm emits same-update source-domain pairs
  {seq, gazebo_fdm_timestamp_ns, ardupilot_time_now_us}
to 127.0.0.1:WITNESS_UDP_PORT.  This process reads that datagram stream on a
non-blocking socket (so it can never backpressure ArduPilot), and relays each
pair over a loopback TCP stream to the Windows-side collector.

Contains ONLY timing values provenanced by the SIM_JSON clock witness.  No
pose, attitude, world state, or survivor data exists on this stream.
"""

from __future__ import annotations

import argparse
from collections import deque
import os
import select
import socket
import threading


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--udp-port", type=int, required=True)
    parser.add_argument("--tcp-port", type=int, required=True)
    parser.add_argument("--pid-file", required=True)
    args = parser.parse_args()
    with open(args.pid_file, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))

    queue: deque[bytes] = deque(maxlen=1048576)
    condition = threading.Condition()
    metrics = {"received": 0, "relayed": 0, "dropped": 0}

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setblocking(False)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(("0.0.0.0", args.udp_port))

    def udp_pump() -> None:
        while True:
            try:
                datagram, _ = udp.recvfrom(4096)
            except BlockingIOError:
                select.select([udp], [], [], 0.1)
                continue
            except OSError:
                return
            decoded = datagram.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            with condition:
                metrics["received"] += 1
                if len(queue) >= queue.maxlen:  # type: ignore[attr-defined]
                    metrics["dropped"] += len(queue)
                    queue.clear()
                queue.append(decoded.encode("utf-8"))
                condition.notify()

    threading.Thread(target=udp_pump, name="clock-witness-udp", daemon=True).start()

    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", args.tcp_port))
        server.listen(1)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2.0)
                while True:
                    with condition:
                        if not queue:
                            condition.wait(timeout=0.5)
                        if not queue:
                            continue
                        items = list(queue)
                        queue.clear()
                        metrics["relayed"] += len(items)
                    payload = "".join(item.decode("utf-8") + "\n" for item in items)
                    try:
                        connection.sendall(payload.encode("utf-8"))
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        break


if __name__ == "__main__":
    raise SystemExit(main())