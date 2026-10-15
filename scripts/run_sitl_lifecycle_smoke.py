import time
import json
from pathlib import Path
from ground.backend.app.sitl.launcher import SITLLauncher
from ground.backend.app.sitl.adapter import SITLAutopilotAdapter
from ground.backend.app.domain.mission import GeoPoint
import psutil
import subprocess

def check_stale_processes():
    result = subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", "pgrep -f 'sim_vehicle.py|arducopter|mavproxy' || true"], capture_output=True, text=True)
    return bool(result.stdout.strip())

def run_smoke():
    output = []
    for i in range(3):
        print(f"Cycle {i+1}/3")
        launcher = SITLLauncher(ardupilot_home="/home/nandakishore7/ardupilot")
        location = GeoPoint(latitude=-35.363261, longitude=149.165230, altitude_m=584.0)
        log_path = Path(f"sitl_smoke_{i}.log")
        
        t0 = time.time()
        launcher.start(location, 0.0, log_path)
        
        adapter = SITLAutopilotAdapter("tcp:127.0.0.1:5760", "sitl", "simulation_only", 40.0, 10.0)
        identity = None
        hb_count = 0
        try:
            identity = adapter.connect()
            t_hb = time.time() - t0
            print(f"  Got first heartbeat in {t_hb:.2f}s")
            
            t_end = time.time() + 30
            while time.time() < t_end:
                msg = adapter.router.wait_for("HEARTBEAT", timeout_s=1.0)
                if msg:
                    hb_count += 1
        except Exception as e:
            print(f"  Error: {e}")
            t_hb = -1
        finally:
            adapter.disconnect()
            launcher.stop()
        
        time.sleep(2) # Give it time to kill
        stale = check_stale_processes()
        
        res = {
            "cycle": i+1,
            "success": hb_count >= 25,
            "heartbeat_latency_s": t_hb,
            "heartbeat_count": hb_count,
            "stale_processes_found": stale
        }
        output.append(res)
        print(" ", res)
        if not res["success"]:
            print("  FAILED CYCLE")
            break

    with open("PHASE4_SITL_LIFECYCLE_EVIDENCE.json", "w") as f:
        json.dump({
            "root_cause": "WSL processes (sim_vehicle.py, arducopter) remained orphaned after Windows process termination, holding the TCP port and preventing subsequent launches.",
            "repair": "Modified SITLLauncher.stop() to explicitly run `killall -9 sim_vehicle.py arducopter mavproxy.py` inside WSL on Windows before terminating the wrapper.",
            "cycles": output
        }, f, indent=2)

if __name__ == "__main__":
    run_smoke()
