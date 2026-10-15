"""Official ArduPilot sim_vehicle.py launcher without vendoring ArduPilot."""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..domain.mission import GeoPoint


class SITLLaunchError(RuntimeError):
    pass


@dataclass
class ArduPilotSourceIdentity:
    home: str
    commit: str | None
    branch: str | None


class SITLLauncher:
    def __init__(
        self,
        ardupilot_home: str | None = None,
        vehicle: str = "ArduCopter",
        frame: str = "quad",
        speedup: int = 1,
        wsl_distribution: str = "Ubuntu",
    ):
        self.ardupilot_home = ardupilot_home or os.environ.get("ARDUPILOT_HOME", "")
        self.vehicle = vehicle
        self.frame = frame
        self.speedup = speedup
        self.wsl_distribution = wsl_distribution
        self.process: subprocess.Popen[str] | None = None
        self.log_handle = None
        self._cached_identity: ArduPilotSourceIdentity | None = None

    def detect(self) -> ArduPilotSourceIdentity | None:
        if self._cached_identity is not None:
            return self._cached_identity
        if not self.ardupilot_home:
            return None
        if platform.system() == "Windows" and self.ardupilot_home.startswith("/"):
            if not self._wsl_file_exists(f"{self.ardupilot_home}/Tools/autotest/sim_vehicle.py"):
                return None
            self._cached_identity = ArduPilotSourceIdentity(
                self.ardupilot_home,
                self._wsl_git("rev-parse HEAD"),
                self._wsl_git("branch --show-current"),
            )
            return self._cached_identity
        home = Path(self.ardupilot_home).expanduser().resolve()
        if not (home / "Tools" / "autotest" / "sim_vehicle.py").is_file():
            return None
        self._cached_identity = ArduPilotSourceIdentity(
            str(home), self._git(home, "rev-parse", "HEAD"), self._git(home, "branch", "--show-current")
        )
        return self._cached_identity

    def start(self, location: GeoPoint, heading_deg: float, log_path: Path) -> ArduPilotSourceIdentity:
        identity = self.detect()
        if identity is None:
            raise SITLLaunchError("Official ArduPilot sim_vehicle.py was not found; set ARDUPILOT_HOME")
        if self.process and self.process.poll() is None:
            raise SITLLaunchError("An ArduPilot SITL process is already running")
        if location.altitude_m is None:
            raise SITLLaunchError("SITL custom launch location requires altitude")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("w", encoding="utf-8")
        custom = f"{location.latitude:.7f},{location.longitude:.7f},{location.altitude_m:.1f},{heading_deg:.1f}"
        arguments = [
            "Tools/autotest/sim_vehicle.py", "-v", self.vehicle, "-f", self.frame,
            "--custom-location", custom, "--speedup", str(self.speedup),
            "--out", "tcpin:0.0.0.0:14552",  # Port for Python Script, avoids 5762 clash
        ]
        if platform.system() == "Windows" and identity.home.startswith("/"):
            command = " ".join(shlex.quote(value) for value in arguments)
            self.process = subprocess.Popen(
                ["wsl.exe", "-d", self.wsl_distribution, "--", "bash", "-lc", f"cd {shlex.quote(identity.home)} && {command}"],
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        else:
            self.process = subprocess.Popen(
                arguments,
                cwd=identity.home,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        time.sleep(0.5)
        if self.process.poll() is not None:
            self.stop()
            raise SITLLaunchError(f"ArduPilot SITL exited during startup; inspect {log_path}")
        return identity

    def stop(self, timeout_s: float = 10) -> None:
        if self.process and self.process.poll() is None:
            if platform.system() == "Windows":
                subprocess.run(
                    ["wsl.exe", "-d", self.wsl_distribution, "--", "bash", "-lc", "pkill -9 -f 'sim_vehicle|arducopter|mavproxy' || true"],
                    capture_output=True,
                    check=False,
                )
            self.process.terminate()
            try:
                self.process.wait(timeout_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None

    def status(self) -> dict[str, object]:
        identity = self.detect()
        return {
            "installed": identity is not None,
            "running": self.process is not None and self.process.poll() is None,
            "pid": self.process.pid if self.process and self.process.poll() is None else None,
            "source": identity.__dict__ if identity else None,
        }

    def _wsl_file_exists(self, path: str) -> bool:
        result = subprocess.run(
            ["wsl.exe", "-d", self.wsl_distribution, "--", "test", "-f", path],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def _wsl_git(self, arguments: str) -> str | None:
        result = subprocess.run(
            ["wsl.exe", "-d", self.wsl_distribution, "--", "bash", "-lc", f"git -C {shlex.quote(self.ardupilot_home)} {arguments}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or None

    @staticmethod
    def _git(home: Path, *arguments: str) -> str | None:
        result = subprocess.run(["git", "-C", str(home), *arguments], capture_output=True, text=True, check=False)
        return result.stdout.strip() or None
