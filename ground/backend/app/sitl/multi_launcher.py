"""Official local ArduPilot multi-vehicle launcher using installed CLI evidence."""

from __future__ import annotations

import platform
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..domain.mission import GeoPoint
from .adapter import SITLAutopilotAdapter
from .guard import SITLSafetyGuard
from .launcher import ArduPilotSourceIdentity, SITLLaunchError, SITLLauncher


@dataclass(frozen=True)
class MultiSITLInstance:
    vehicle_id: str
    instance_id: int
    expected_system_id: int
    connection: str


class MultiSITLLauncher(SITLLauncher):
    """Starts one sim_vehicle process managing N isolated ArduCopters.

    Endpoint derivation (5760 + 10*instance) and supported flags were verified
    from the installed ArduPilot sim_vehicle.py before Stage 9S implementation.
    Identity is still checked from each real heartbeat before fleet readiness.
    """

    def __init__(self, *args, base_port: int = 5760, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_port = base_port
        self.instances: list[MultiSITLInstance] = []

    def endpoints(self, count: int) -> list[MultiSITLInstance]:
        if count < 2:
            raise SITLLaunchError("MultiSITL requires at least two instances")
        result = [
            MultiSITLInstance(
                vehicle_id=f"drone_{index + 1:02d}",
                instance_id=index,
                expected_system_id=index + 1,
                connection=f"tcp:127.0.0.1:{self.base_port + index * 10}",
            )
            for index in range(count)
        ]
        guard = SITLSafetyGuard("sitl", "simulation_only")
        for instance in result:
            guard.validate(instance.connection)
        return result

    def start_fleet(
        self,
        count: int,
        location: GeoPoint,
        heading_deg: float,
        log_path: Path,
        offset_bearing_deg: float = 90,
        offset_distance_m: float = 2,
    ) -> ArduPilotSourceIdentity:
        identity = self.detect()
        if identity is None:
            raise SITLLaunchError("Official ArduPilot sim_vehicle.py was not found; set ARDUPILOT_HOME")
        if self.process and self.process.poll() is None:
            raise SITLLaunchError("An ArduPilot SITL process is already running")
        if location.altitude_m is None:
            raise SITLLaunchError("SITL custom launch location requires altitude")
        self.instances = self.endpoints(count)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("w", encoding="utf-8")
        custom = f"{location.latitude:.7f},{location.longitude:.7f},{location.altitude_m:.1f},{heading_deg:.1f}"
        arguments = [
            "Tools/autotest/sim_vehicle.py", "-v", self.vehicle, "-f", self.frame,
            "--no-mavproxy", "--custom-location", custom, "--speedup", str(self.speedup),
            "--count", str(count), "--auto-sysid",
            "--auto-offset-line", f"{offset_bearing_deg:g},{offset_distance_m:g}",
        ]
        if platform.system() == "Windows" and identity.home.startswith("/"):
            command = " ".join(shlex.quote(value) for value in arguments)
            self.process = subprocess.Popen(
                ["wsl.exe", "-d", self.wsl_distribution, "--", "bash", "-lc", f"cd {shlex.quote(identity.home)} && {command}"],
                stdout=self.log_handle, stderr=subprocess.STDOUT, text=True,
            )
        else:
            self.process = subprocess.Popen(
                arguments, cwd=identity.home, stdout=self.log_handle, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            )
        time.sleep(.5)
        if self.process.poll() is not None:
            self.stop()
            raise SITLLaunchError(f"ArduPilot multi-SITL exited during startup; inspect {log_path}")
        return identity

    def wait_endpoints(self, timeout_s: float = 90) -> None:
        deadline = time.monotonic() + timeout_s
        pending = {instance.connection: instance for instance in self.instances}
        while pending and time.monotonic() < deadline:
            for connection, instance in list(pending.items()):
                port = int(connection.rsplit(":", 1)[1])
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        pending.pop(connection)
                except OSError:
                    pass
            if pending:
                time.sleep(.25)
        if pending:
            raise TimeoutError("Multi-SITL endpoints not ready: " + ", ".join(pending))

    def verify_heartbeats(self, timeout_s: float = 30) -> list[dict[str, object]]:
        identities: list[dict[str, object]] = []
        seen_sysids: set[int] = set()
        for instance in self.instances:
            adapter = SITLAutopilotAdapter(instance.connection, "sitl", "simulation_only", timeout_s, 10)
            try:
                identity = adapter.connect()
            finally:
                adapter.disconnect()
            system_id = int(identity["system_id"])
            if system_id != instance.expected_system_id:
                raise SITLLaunchError(
                    f"{instance.vehicle_id} expected SYSID {instance.expected_system_id}, received {system_id}"
                )
            if system_id in seen_sysids:
                raise SITLLaunchError(f"Duplicate MAVLink SYSID detected: {system_id}")
            seen_sysids.add(system_id)
            identities.append({**instance.__dict__, **identity})
        return identities
