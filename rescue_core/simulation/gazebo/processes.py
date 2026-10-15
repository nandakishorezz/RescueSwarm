"""Owned WSL Gazebo/SITL processes with deterministic teardown."""

from __future__ import annotations

import os
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ground.backend.app.sitl.guard import SITLSafetyGuard


def windows_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    posix = resolved.as_posix()
    # Handle WSL UNC paths: //wsl.localhost/Ubuntu/... -> /...
    for prefix in ("//wsl.localhost/Ubuntu", "//wsl$/Ubuntu"):
        if posix.startswith(prefix):
            return posix[len(prefix):]
    # Standard Windows drive letter path: C:/... -> /mnt/c/...
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{posix.split(':', 1)[1].lstrip('/')}"


@dataclass
class OwnedSimulationProcesses:
    processes: dict[str, subprocess.Popen[str]] = field(default_factory=dict)
    linux_pid_files: dict[str, Path] = field(default_factory=dict)

    def add(self, name: str, process: subprocess.Popen[str], linux_pid_file: Path | None = None) -> None:
        self.processes[name] = process
        if linux_pid_file:
            self.linux_pid_files[name] = linux_pid_file

    def running(self, name: str) -> bool:
        process = self.processes.get(name)
        return process is not None and process.poll() is None

    def stop(self, timeout_s: float = 8.0) -> None:
        prefix = ["wsl.exe", "-e"] if os.name == "nt" else []
        for name, pid_path in self.linux_pid_files.items():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            # Kill process and all its children
            try:
                subprocess.run(prefix + ["bash", "-c", f"pkill -TERM -P {pid} || true"], check=False, capture_output=True, timeout=5.0)
                subprocess.run(prefix + ["kill", "-TERM", str(pid)], check=False, capture_output=True, timeout=5.0)
            except Exception:
                pass
            
        for process in reversed(list(self.processes.values())):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + timeout_s
        for process in reversed(list(self.processes.values())):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
                
        # Hard kill if still alive
        for name, pid_path in self.linux_pid_files.items():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            try:
                subprocess.run(prefix + ["bash", "-c", f"pkill -9 -P {pid} || true"], check=False, capture_output=True, timeout=5.0)
                subprocess.run(prefix + ["kill", "-9", str(pid)], check=False, capture_output=True, timeout=5.0)
            except Exception:
                pass
            
        try:
            subprocess.run(prefix + ["bash", "-c", "pkill -9 -f 'socat tcp-listen:5761' || true"], check=False, capture_output=True, timeout=5.0)
        except Exception:
            pass

        self.processes.clear()
        self.linux_pid_files.clear()


class WslGazeboLauncher:
    """Launch only locally generated Gazebo and JSON-model ArduCopter SITL."""

    def __init__(
        self,
        ardupilot_path: str = "/home/nandakishore7/ardupilot",
        plugin_path: str = "/home/nandakishore7/ardupilot_gazebo",
        connection: str = "tcp:127.0.0.1:5760",
        clock_witness_udp_port: int | None = None,
    ) -> None:
        SITLSafetyGuard(environment="sitl", command_authority="simulation_only").validate(connection)
        self.ardupilot_path = ardupilot_path
        self.plugin_path = plugin_path
        self.connection = "tcp:127.0.0.1:5761" if (os.name == "nt" and connection == "tcp:127.0.0.1:5760") else connection
        # Phase-2C: when set, SIM_JSON emits same-update source-domain clock
        # witness datagrams to 127.0.0.1:<port> on every 5th FDM update.
        self.clock_witness_udp_port = clock_witness_udp_port

    def start(self, run_dir: Path, fleet_count: int = 1) -> OwnedSimulationProcesses:
        import subprocess
        try:
            uname = subprocess.run(["wsl.exe", "-e", "uname", "-r"], capture_output=True, text=True, check=True).stdout
            if "microsoft" not in uname.lower() and "wsl" not in uname.lower():
                raise RuntimeError("Stage 13SIM launcher currently requires the inspected WSL2 environment")
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise RuntimeError("Stage 13SIM launcher currently requires the inspected WSL2 environment")
        world = windows_to_wsl(run_dir / "generated_world.sdf")
        existing_worlds = self._workspace_gazebo_processes()
        if existing_worlds:
            raise RuntimeError(
                "Refusing contaminated simulation startup; owned Gazebo process already exists: "
                + ", ".join(existing_worlds)
            )
        vehicle_assets = windows_to_wsl(run_dir / "models")
        logs = windows_to_wsl(run_dir)
        (run_dir / "gazebo.log").touch()
        (run_dir / "arducopter.log").touch()
        georef = json.loads((run_dir / "georeference.json").read_text(encoding="utf-8"))
        latitude, longitude, altitude = georef["origin_wgs84"]
        custom_location = f"{float(latitude):.7f},{float(longitude):.7f},{float(altitude):.1f},0"
        phase1_params = windows_to_wsl(Path(__file__).resolve().parents[3] / "simulation" / "profiles" / "arducopter_gazebo_phase1.parm")
        sensor_params_path = run_dir / "sensor_realism.parm"
        sensor_params = windows_to_wsl(sensor_params_path) if sensor_params_path.is_file() else None
        sensor_seed_path = run_dir / "sensor_seed.json"
        sensor_seed = (
            int(json.loads(sensor_seed_path.read_text(encoding="utf-8"))["sensor_seed"])
            if sensor_seed_path.is_file() else 1
        )
        gazebo_pid = run_dir / "gazebo.pid"
        sitl_pid = run_dir / "arducopter.pid"
        gazebo_script = run_dir / "launch_gazebo.sh"
        gazebo_content = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
            "export GZ_VERSION=harmonic\n"
            f"export GZ_SIM_SYSTEM_PLUGIN_PATH={self.plugin_path}/build\n"
            f"export GZ_SIM_RESOURCE_PATH={vehicle_assets}:{self.plugin_path}/models:{self.plugin_path}/worlds\n"
            "export LIBGL_ALWAYS_SOFTWARE=${LIBGL_ALWAYS_SOFTWARE:-0}\n"
            f"echo $$ > {windows_to_wsl(gazebo_pid)}\n"
            f"exec gz sim -s -r --headless-rendering -v 3 --seed {self._seed_for_run(run_dir)} {world} > {logs}/gazebo.log 2>&1\n"
        )
        self._write_wsl_script(gazebo_script, gazebo_content)
        sitl_script = run_dir / "launch_arducopter.sh"
        witness_env = (
            f"export SIM_JSON_CLOCK_WITNESS_PORT={self.clock_witness_udp_port}\n"
            if self.clock_witness_udp_port else ""
        )
        definition_path = run_dir / "simulation_run_definition.json"
        definition = json.loads(definition_path.read_text(encoding="utf-8")) if definition_path.is_file() else {}
        fleet_offset_m = float(definition.get("fleet_spawn_offset_m", 2.0))
        sitl_content = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
            f"export SIM_SENSOR_SEED={sensor_seed}\n"
            f"cd {self.ardupilot_path}\n"
            f"echo $$ > {windows_to_wsl(sitl_pid)}\n"
            + witness_env
            + "pkill -9 -f 'socat tcp-listen:5761' || true\n"
            + "socat tcp-listen:5761,reuseaddr,bind=0.0.0.0,fork tcp:127.0.0.1:5760 &\n"
            + f"exec python3 Tools/autotest/sim_vehicle.py -w -v ArduCopter -f gazebo-iris --model JSON --no-mavproxy "
            "--add-param-file=Tools/autotest/default_params/gazebo-iris.parm "
            f"--add-param-file={phase1_params} "
            + (f"--add-param-file={sensor_params} " if sensor_params else "")
            + f"--custom-location={custom_location} "
            + (f"--count {fleet_count} --auto-sysid --auto-offset-line 90,{fleet_offset_m:g} " if fleet_count > 1 else "")
            + f"> {logs}/arducopter.log 2>&1\n"
        )
        self._write_wsl_script(sitl_script, sitl_content)
        owned = OwnedSimulationProcesses()
        prefix = ["wsl.exe", "-e"] if os.name == "nt" else []
        owned.add("gazebo", subprocess.Popen(
            prefix + ["bash", windows_to_wsl(gazebo_script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        ), gazebo_pid)
        time.sleep(3)
        if not owned.running("gazebo"):
            owned.stop()
            raise RuntimeError("Gazebo exited during startup; inspect gazebo.log")
        server_pids = self._processes_with_argument(world)
        if not server_pids:
            owned.stop()
            raise RuntimeError("Could not identify the owned Gazebo server process")
        server_pid_file = run_dir / "gazebo.server.pid"
        server_pid_file.write_text(str(server_pids[-1]), encoding="utf-8")
        owned.linux_pid_files["gazebo_server"] = server_pid_file
        owned.add("arducopter", subprocess.Popen(
            prefix + ["bash", windows_to_wsl(sitl_script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        ), sitl_pid)
        return owned

    @staticmethod
    def _processes_with_argument(argument: str) -> list[int]:
        script = (
            "import os,sys\nneedle=sys.argv[1].encode()\n"
            "for name in os.listdir('/proc'):\n"
            "  if not name.isdigit(): continue\n"
            "  try: data=open('/proc/'+name+'/cmdline','rb').read()\n"
            "  except OSError: continue\n"
            "  if needle in data: print(name)\n"
        )
        prefix = ["wsl.exe", "-e"] if os.name == "nt" else []
        result = subprocess.run(
            prefix + ["python3", "-c", script, argument],
            text=True, capture_output=True, check=False, timeout=10,
        )
        return [int(line) for line in result.stdout.splitlines() if line.isdigit()]

    @staticmethod
    def _workspace_gazebo_processes() -> list[str]:
        marker = "/SURVIVOR_DETECTION/data/simulation_runs/"
        script = (
            "import os,sys\nmarker=sys.argv[1].encode()\n"
            "for name in os.listdir('/proc'):\n"
            "  if not name.isdigit(): continue\n"
            "  try: data=open('/proc/'+name+'/cmdline','rb').read()\n"
            "  except OSError: continue\n"
            "  parts=[p for p in data.split(b'\\0') if p]\n"
            "  is_gz_sim=(b'sim' in parts and any(p==b'gz' or p.endswith(b'/gz') for p in parts))\n"
            "  if marker in data and is_gz_sim: print(name+':'+data.replace(b'\\0',b' ').decode(errors='replace'))\n"
        )
        prefix = ["wsl.exe", "-e"] if os.name == "nt" else []
        result = subprocess.run(
            prefix + ["python3", "-c", script, marker],
            text=True, capture_output=True, check=False, timeout=10,
        )
        return result.stdout.splitlines()

    @staticmethod
    def wait_for_topic(expected_suffix: str, timeout_s: float = 30.0) -> str:
        deadline = time.monotonic() + timeout_s
        prefix = ["wsl.exe", "-e"] if os.name == "nt" else []
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(prefix + ["gz", "topic", "-l"], text=True, capture_output=True, check=False, timeout=15)
                topics = result.stdout.splitlines()
                match = next((topic for topic in topics if topic.endswith(expected_suffix)), None)
                if match:
                    return match
            except subprocess.TimeoutExpired:
                pass
            time.sleep(.5)
        raise TimeoutError(f"Gazebo sensor topic did not appear: *{expected_suffix}")

    @staticmethod
    def _seed_for_run(run_dir: Path) -> int:
        payload = json.loads((run_dir / "environment_profile.json").read_text(encoding="utf-8"))
        return int(payload.get("random_seed", 0))

    @staticmethod
    def _write_wsl_script(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)
