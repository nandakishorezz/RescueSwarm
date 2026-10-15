"""Read-only simulator environment discovery."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from pydantic import BaseModel


class SimulatorEnvironmentManifest(BaseModel):
    host_os: str
    linux_os: str
    wsl: bool
    kernel: str
    gazebo_version: str
    gz_sim_version: str
    ardupilot_gazebo_path: str | None
    ardupilot_gazebo_commit: str
    ardupilot_path: str | None
    ardupilot_version: str
    ardupilot_commit: str
    render_engine: str
    gpu_opengl: str
    gstreamer_version: str
    cmake_version: str
    python_version: str


def _run(arguments: list[str], timeout: float = 10) -> str:
    try:
        result = subprocess.run(arguments, text=True, capture_output=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "NOT_FOUND"
    text = (result.stdout or result.stderr).strip()
    return text if text else ("AVAILABLE" if result.returncode == 0 else "NOT_FOUND")


def _wsl(*arguments: str, timeout: float = 10) -> str:
    return _run(["wsl.exe", "-d", "Ubuntu", "--", *arguments], timeout=timeout)


def inspect_simulator_environment(
    ardupilot_path: str = "/home/nandakishore7/ardupilot",
    plugin_path: str = "/home/nandakishore7/ardupilot_gazebo",
) -> SimulatorEnvironmentManifest:
    wsl = os.name == "nt"
    command = _wsl if wsl else lambda *args, timeout=10: _run(list(args), timeout=timeout)
    os_release = command("cat", "/etc/os-release")
    linux_os = "UNKNOWN"
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            linux_os = line.partition("=")[2].strip().strip('"')
            break
    kernel = command("uname", "-a")
    gz = command("gz", "sim", "--versions", timeout=20)
    plugin_exists = command("test", "-d", plugin_path) == "AVAILABLE"
    ardupilot_exists = command("test", "-d", ardupilot_path) == "AVAILABLE"
    gl = command("glxinfo", "-B", timeout=20)
    render = "OGRE2"
    if "llvmpipe" in gl.lower():
        render += " / SOFTWARE LLVMPipe"
    elif gl == "NOT_FOUND":
        render += " / OPENGL NOT_PROBED"
    else:
        render += " / OPENGL AVAILABLE"
    gazebo_family = "Gazebo Harmonic" if gz.split(".", 1)[0] == "8" else "Gazebo (family not mapped)"
    return SimulatorEnvironmentManifest(
        host_os=platform.platform(),
        linux_os=linux_os,
        wsl=wsl,
        kernel=kernel,
        gazebo_version=gazebo_family,
        gz_sim_version=gz,
        ardupilot_gazebo_path=plugin_path if plugin_exists else None,
        ardupilot_gazebo_commit=command("git", "-C", plugin_path, "rev-parse", "HEAD") if plugin_exists else "NOT_INSTALLED",
        ardupilot_path=ardupilot_path if ardupilot_exists else None,
        ardupilot_version=command(
            "bash", "-c",
            f"grep '^#define FIRMWARE_VERSION ' {ardupilot_path}/ArduCopter/version.h | sed 's/^#define FIRMWARE_VERSION //'",
        ) if ardupilot_exists else "NOT_FOUND",
        ardupilot_commit=command("git", "-C", ardupilot_path, "rev-parse", "HEAD") if ardupilot_exists else "NOT_FOUND",
        render_engine=render,
        gpu_opengl=gl,
        gstreamer_version=command("gst-launch-1.0", "--version"),
        cmake_version=command("cmake", "--version"),
        python_version=command("python3", "--version"),
    )
