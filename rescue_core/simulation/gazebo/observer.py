"""Bounded observer-only capture of Gazebo world truth."""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def _quat(value: dict[str, Any]) -> tuple[float, float, float, float]:
    return (float(value.get("w", 1)), float(value.get("x", 0)),
            float(value.get("y", 0)), float(value.get("z", 0)))


def _multiply(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw)


def _rotate(q: tuple[float, ...], v: tuple[float, float, float]) -> tuple[float, float, float]:
    result = _multiply(_multiply(q, (0.0, *v)), (q[0], -q[1], -q[2], -q[3]))
    return result[1], result[2], result[3]


class GazeboTruthObserver:
    """Capture one vehicle and attached camera at a bounded simulation-time rate."""

    def __init__(self, world: str, vehicle: str, output: Path, interval_s: float = 0.1) -> None:
        self.world, self.vehicle, self.output, self.interval_s = world, vehicle, output, interval_s
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.samples = 0
        self.first_sim_time_s: float | None = None
        self.last_sim_time_s: float | None = None
        self.started_wall = 0.0

    def start(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.started_wall = time.monotonic()
        topic = f"/world/{self.world}/pose/info"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            listed = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "gz", "topic", "-l"],
                capture_output=True, text=True, timeout=10,
            )
            if topic in listed.stdout.splitlines():
                break
            time.sleep(0.1)
        self.process = subprocess.Popen(
            ["wsl.exe", "-d", "Ubuntu", "--", "gz", "topic", "--json-output", "-e", "-t", topic],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.thread = threading.Thread(target=self._read, name="gazebo-truth-observer", daemon=True)
        self.thread.start()

    def stop(self) -> dict[str, float | int | None]:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(2)
        if self.thread: self.thread.join(3)
        sim_duration = None
        if self.first_sim_time_s is not None and self.last_sim_time_s is not None:
            sim_duration = self.last_sim_time_s - self.first_sim_time_s
        wall_duration = max(0.0, time.monotonic() - self.started_wall)
        return {"truth_samples": self.samples, "simulation_duration_s": sim_duration,
                "wall_duration_s": wall_duration,
                "observed_real_time_factor": sim_duration / wall_duration if sim_duration is not None and wall_duration else None}

    def _read(self) -> None:
        assert self.process and self.process.stdout
        last_saved = -math.inf
        with self.output.open("w", encoding="utf-8", newline="\n") as stream:
            for line in self.process.stdout:
                if self.stop_event.is_set(): break
                try:
                    message = json.loads(line)
                    poses = {item.get("name"): item for item in message.get("pose", [])}
                    vehicle = poses.get(self.vehicle)
                    if vehicle is None: continue
                    # PosePublisher stamps each pose rather than the vector.
                    stamp = vehicle.get("header", {}).get("stamp", message.get("header", {}).get("stamp", {}))
                    sim_time = float(stamp.get("sec", 0)) + float(stamp.get("nsec", 0)) / 1e9
                    if sim_time <= last_saved + self.interval_s - 1e-6: continue
                    position = vehicle.get("position", {}); vehicle_q = _quat(vehicle.get("orientation", {}))
                    vehicle_position = [float(position.get(axis, 0)) for axis in ("x", "y", "z")]
                    record: dict[str, Any] = {
                        "classification": "SIMULATION_ONLY_GROUND_TRUTH", "sim_time_s": sim_time,
                        "wall_time_s": time.monotonic() - self.started_wall,
                        "vehicle_position_gazebo_m": vehicle_position,
                        "vehicle_quaternion_gazebo": list(vehicle_q),
                    }
                    camera = next((item for name, item in poses.items() if str(name).endswith("::camera_link")), poses.get("camera_link"))
                    if camera is not None:
                        local_value = camera.get("position", {})
                        local = tuple(float(local_value.get(axis, 0)) for axis in ("x", "y", "z"))
                        rotated = _rotate(vehicle_q, local)
                        record["camera_position_gazebo_m"] = [vehicle_position[i] + rotated[i] for i in range(3)]
                        record["camera_quaternion_gazebo"] = list(_multiply(vehicle_q, _quat(camera.get("orientation", {}))))
                    stream.write(json.dumps(record, separators=(",", ":")) + "\n"); stream.flush()
                    last_saved = sim_time
                    if self.first_sim_time_s is None: self.first_sim_time_s = sim_time
                    self.last_sim_time_s = sim_time; self.samples += 1
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
