"""Forward Gazebo camera pixels and metadata from WSL to the Windows GCS."""

import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

from gz.msgs10.image_pb2 import Image
from gz.transport13 import Node

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 45678
TOPIC = sys.argv[2] if len(sys.argv) > 2 else "/drone_01/ov9281_reference_simulation/image"


def windows_host() -> str:
    """WSL2 loopback is not Windows loopback; use the actual host gateway."""
    if configured := os.environ.get("RESCUESWARM_CAMERA_BRIDGE_HOST"):
        return configured
    try:
        route = subprocess.check_output(["ip", "route", "show", "default"], text=True, timeout=2)
        if match := re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", route):
            return match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    # Older WSL NAT configurations commonly expose the same gateway through
    # resolv.conf.  It is a fallback only: corporate/VPN DNS can point there
    # at a non-host resolver, as happened in the current environment.
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as resolver:
            for line in resolver:
                fields = line.split()
                if len(fields) == 2 and fields[0] == "nameserver" and not fields[1].startswith("127."):
                    return fields[1]
    except OSError:
        pass
    return "127.0.0.1"


HOST = windows_host()
HEADER = struct.Struct("<4sdIIIII")
sock: socket.socket | None = None
frames_sent = 0
send_lock = threading.Lock()


def connect() -> socket.socket:
    while True:
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        candidate.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            candidate.connect((HOST, PORT))
            print(f"[camera-bridge] connected to {HOST}:{PORT}; topic={TOPIC}", flush=True)
            return candidate
        except OSError as exc:
            candidate.close()
            print(f"[camera-bridge] waiting for {HOST}:{PORT}: {exc}", flush=True)
            time.sleep(1)


def cb(msg: Image) -> None:
    global sock, frames_sent
    payload = bytes(msg.data)
    width, height, stride = int(msg.width), int(msg.height), int(msg.step)
    pixel_format = int(msg.pixel_format_type)
    if width <= 0 or height <= 0 or stride <= 0 or not payload:
        print(f"[camera-bridge] ignored invalid image {width}x{height} stride={stride} bytes={len(payload)}", flush=True)
        return
    packet = HEADER.pack(b"CAM2", time.time(), width, height, stride, pixel_format, len(payload)) + payload
    with send_lock:
        while True:
            if sock is None:
                sock = connect()
            try:
                sock.sendall(packet)
                frames_sent += 1
                if frames_sent % 120 == 0:
                    print(f"[camera-bridge] frames={frames_sent} {width}x{height} stride={stride} format={pixel_format} bytes={len(payload)}", flush=True)
                return
            except OSError as exc:
                print(f"[camera-bridge] connection lost: {exc}; reconnecting", flush=True)
                sock.close()
                sock = None


print(f"[camera-bridge] starting; host={HOST} port={PORT} topic={TOPIC}", flush=True)
node = Node()
if not node.subscribe(Image, TOPIC, cb):
    raise RuntimeError(f"could not subscribe to Gazebo camera topic: {TOPIC}")
threading.Event().wait()
