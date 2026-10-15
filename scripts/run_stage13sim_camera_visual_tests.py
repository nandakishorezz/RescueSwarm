"""Render pixel-only observer evidence for Phase-1 camera geometry (no detector)."""

from __future__ import annotations

import base64
import json
import math
import os
import shlex
import subprocess
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from rescue_core.simulation.profiles import load_camera_profile
from rescue_core.simulation.world.builders import _camera_link_euler

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "simulation_camera_tests"
TOPIC = "/stage13_camera_test/image"


def _wsl_path(path: Path) -> str:
    """Translate an absolute Windows path without depending on shell quoting."""
    resolved = path.resolve().as_posix()
    if len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/mnt/{resolved[0].lower()}/{resolved[3:]}"
    return resolved


def _decode_json_stream(payload: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    messages: list[dict[str, object]] = []
    cursor = 0
    while cursor < len(payload):
        while cursor < len(payload) and payload[cursor].isspace():
            cursor += 1
        if cursor >= len(payload):
            break
        message, cursor = decoder.raw_decode(payload, cursor)
        messages.append(message)
    return messages


def qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw)


def world(case: str, survivor_xy: tuple[float, float], camera_q) -> str:
    camera = load_camera_profile(ROOT / "simulation" / "profiles" / "ov9281_reference_simulation.yaml")
    hfov, _ = camera.derived_fov(); roll, pitch, yaw = _camera_link_euler(camera_q)
    return f"""<?xml version='1.0'?>
<sdf version='1.9'><world name='camera_{case}'>
  <gravity>0 0 -9.80665</gravity>
  <plugin filename='gz-sim-physics-system' name='gz::sim::systems::Physics'/>
  <plugin filename='gz-sim-scene-broadcaster-system' name='gz::sim::systems::SceneBroadcaster'/>
  <plugin filename='gz-sim-sensors-system' name='gz::sim::systems::Sensors'><render_engine>ogre2</render_engine></plugin>
  <scene><ambient>0.45 0.45 0.45 1</ambient><background>0.72 0.84 0.92 1</background><shadows>true</shadows></scene>
  <light name='sun' type='directional'><pose>0 0 30 0 0 0</pose><direction>-0.4 0.2 -0.9</direction><cast_shadows>true</cast_shadows></light>
  <model name='ground'><static>true</static><link name='ground'><visual name='ground'><geometry><box><size>80 80 0.1</size></box></geometry><material><diffuse>0.23 0.34 0.20 1</diffuse></material></visual></link></model>
  <model name='survivor'><static>true</static><pose>{survivor_xy[0]} {survivor_xy[1]} 0 0 0 0</pose><link name='person'>
    <visual name='torso'><pose>0 0 .9 0 0 0</pose><geometry><cylinder><radius>.3</radius><length>1.2</length></cylinder></geometry><material><diffuse>1 .12 0 1</diffuse></material></visual>
    <visual name='head'><pose>0 0 1.65 0 0 0</pose><geometry><sphere><radius>.25</radius></sphere></geometry><material><diffuse>.85 .55 .35 1</diffuse></material></visual>
  </link></model>
  <model name='camera_rig'><static>true</static><pose>0 0 8 0 0 0</pose><link name='camera_link'><pose>0 0 0 {roll} {pitch} {yaw}</pose>
    <sensor name='camera' type='camera'><topic>{TOPIC}</topic><always_on>true</always_on><update_rate>{camera.fps.value}</update_rate><camera>
      <horizontal_fov>{hfov}</horizontal_fov><image><width>{camera.resolution_width}</width><height>{camera.resolution_height}</height><format>L_INT8</format></image><clip><near>.05</near><far>100</far></clip>
    </camera></sensor></link></model>
</world></sdf>\n"""


def capture(case: str, survivor_xy, camera_q, *, measure_frequency: bool = False) -> dict[str, object]:
    sdf = OUTPUT / f"{case}.sdf"; sdf.write_text(world(case, survivor_xy, camera_q), encoding="utf-8")
    partition = f"stage13_camera_{case}"
    launch = (
        "echo $$; "
        f"export GZ_PARTITION={shlex.quote(partition)}; "
        f"exec gz sim -s -r --headless-rendering {shlex.quote(_wsl_path(sdf))} >/dev/null 2>&1"
    )
    process = subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", launch],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if process.stdout is None:
        raise RuntimeError("Gazebo launcher did not expose its Linux process ID")
    linux_pid = int(process.stdout.readline().strip())
    gz = ["wsl.exe", "-d", "Ubuntu", "--", "env", f"GZ_PARTITION={partition}", "gz"]
    try:
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            topics = subprocess.run([*gz, "topic", "-l"], capture_output=True, text=True).stdout
            if TOPIC in topics: break
            time.sleep(.25)
        else:
            raise RuntimeError(f"Gazebo camera topic did not appear for {case}")
        result = subprocess.run([*gz, "topic", "--json-output", "-e", "-n", "1", "-t", TOPIC], check=True, capture_output=True, text=True, timeout=20)
        payload = json.loads(result.stdout); raw = base64.b64decode(payload["data"])
        path = OUTPUT / f"{case}.png"; Image.frombytes("L", (int(payload["width"]), int(payload["height"])), raw).save(path)
        pixels = np.frombuffer(raw, dtype=np.uint8)
        evidence: dict[str, object] = {
            "case": case,
            "frame": str(path.relative_to(ROOT)),
            "minimum": int(pixels.min()),
            "maximum": int(pixels.max()),
            "mean": float(pixels.mean()),
            "unique_values": int(np.unique(pixels).size),
        }
        if measure_frequency:
            frequency = subprocess.run(
                [*gz, "topic", "--json-output", "--echo", "--num", "10", "--topic", TOPIC],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            messages = _decode_json_stream(frequency.stdout)
            timestamps = []
            for message in messages:
                stamp = message.get("header", {}).get("stamp", {})  # type: ignore[union-attr]
                timestamps.append(float(stamp["sec"]) + float(stamp["nsec"]) / 1e9)
            intervals = np.diff(np.asarray(timestamps))
            evidence["frequency_basis"] = "GAZEBO_SIMULATION_TIME"
            evidence["frequency_sample_count"] = len(timestamps)
            evidence["measured_frequency_hz"] = float(1.0 / np.median(intervals)) if len(intervals) else None
        return evidence
    finally:
        subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "--", "kill", "-TERM", str(linux_pid)],
            capture_output=True,
            check=False,
        )
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--", "kill", "-KILL", str(linux_pid)],
                capture_output=True,
                check=False,
            )
            process.kill()
        time.sleep(1)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = (math.sqrt(.5), 0.0, 0.0, math.sqrt(.5))
    tilted = qmul((math.cos(math.radians(20)/2), 0, math.sin(math.radians(20)/2), 0), base)
    rolled = qmul((math.cos(math.radians(15)/2), math.sin(math.radians(15)/2), 0, 0), base)
    results = [
        capture("survivor_inside_fov", (0, 0), base, measure_frequency=True),
        capture("survivor_outside_fov", (25, 25), base),
        capture("camera_pitch_20deg", (0, 0), tilted),
        capture("camera_roll_15deg", (0, 0), rolled),
    ]
    images = [np.asarray(Image.open(ROOT / item["frame"]), dtype=np.int16) for item in results]
    report = {
        "classification": "SIMULATION_OBSERVER_PIXEL_EVIDENCE_ONLY", "detector_connected": False,
        "camera_profile": "ov9281_reference_simulation", "hardware_gate_h1": "PENDING", "cases": results,
        "inside_vs_outside_mean_abs_difference": float(np.abs(images[0]-images[1]).mean()),
        "pitch_change_mean_abs_difference": float(np.abs(images[0]-images[2]).mean()),
        "roll_change_mean_abs_difference": float(np.abs(images[0]-images[3]).mean()),
    }
    measured_frequency = results[0].get("measured_frequency_hz")
    report["frequency_within_tolerance"] = bool(
        measured_frequency is not None and abs(float(measured_frequency) - 30.0) <= 0.5
    )
    report["pass"] = report["frequency_within_tolerance"] and all(value > 0.1 for value in (report["inside_vs_outside_mean_abs_difference"], report["pitch_change_mean_abs_difference"], report["roll_change_mean_abs_difference"]))
    (OUTPUT / "camera_visual_test.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
