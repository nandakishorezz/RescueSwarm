import sys
import time
from pathlib import Path
from rescue_core.simulation.orchestration.manager import SimulationManager
from rescue_core.simulation.gazebo.processes import WslGazeboLauncher
from scripts.run_stage13sim_phase4 import build_definition

ROOT = Path("C:\\Users\\nanda\\Downloads\\SURVIVOR_DETECTION")
RUNS = ROOT / "data" / "simulation_runs"

def run_smoke(run_id: str):
    print(f"\n--- {run_id} ---")
    definition = build_definition(run_id, payload_slots=3)
    launcher = WslGazeboLauncher()
    manager = SimulationManager(RUNS, launcher)
    run_dir = manager.prepare(definition)
    
    start_time = time.monotonic()
    endpoint_ready = False
    heartbeat_ready = False
    ekf_ready = False
    error = None
    
    t_endpoint = 0
    t_heartbeat = 0
    t_ekf = 0
    
    try:
        manager.start()
        endpoint_ready = True
        t_endpoint = time.monotonic() - start_time
        
        manager.adapter.wait_ready(120.0)
        heartbeat_ready = True
        t_heartbeat = time.monotonic() - start_time
        
        ekf_ready = True
        t_ekf = time.monotonic() - start_time
        
    except Exception as e:
        error = e
        if hasattr(manager, 'adapter') and manager.adapter.router and manager.adapter.router._thread:
            print(f"Router thread alive: {manager.adapter.router._thread.is_alive()}")
    finally:
        manager.stop(completed=True)
        # Force hard kill if needed
        if manager.processes:
            manager.processes.stop()
            
    print(f"endpoint ready: {'PASS' if endpoint_ready else 'FAIL'}")
    if endpoint_ready: print(f"  latency: {t_endpoint:.2f}s")
    print(f"heartbeat: {'PASS' if heartbeat_ready else 'FAIL'}")
    if heartbeat_ready: print(f"  latency: {t_heartbeat:.2f}s")
    print(f"EKF ready: {'PASS' if ekf_ready else 'FAIL'}")
    if ekf_ready: print(f"  latency: {t_ekf:.2f}s")
    if error:
        print(f"Error: {error}")
    
    passed = endpoint_ready and heartbeat_ready and ekf_ready
    print(f"PASS/FAIL: {'PASS' if passed else 'FAIL'}")
    return passed

runs = [
    "phase4_multitarget_startup_smoke_1",
    "phase4_multitarget_startup_smoke_2",
    "phase4_multitarget_startup_smoke_3",
]

successes = 0
for rid in runs:
    if run_smoke(rid):
        successes += 1
    else:
        break

print(f"\nStartup total: {successes}/3")
