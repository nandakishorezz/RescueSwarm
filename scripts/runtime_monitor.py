import psutil, time, json
import datetime
import subprocess

def get_procs(names):
    procs = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if any(n in cmd for n in names) or any(n in p.info["name"] for n in names):
                procs.append(p)
        except: pass
    return procs

print("Starting resource monitor...")
start_time = time.time()
while time.time() - start_time < 30:
    cpu_percent = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    
    # Try to find target processes
    gz = get_procs(["gzserver", "gzclient", "ruby", "gazebo"])
    ap = get_procs(["arducopter"])
    py = get_procs(["run_stage13sim_phase4.py", "sitl_smoke_test.py", "python"])
    
    gz_cpu = sum(p.cpu_percent(interval=0) for p in gz)
    ap_cpu = sum(p.cpu_percent(interval=0) for p in ap)
    py_cpu = sum(p.cpu_percent(interval=0) for p in py)
    
    log = {
        "timestamp": datetime.datetime.now().isoformat(),
        "total_cpu": cpu_percent,
        "logical_cores": psutil.cpu_count(),
        "mem_percent": mem.percent,
        "gz_cpu": gz_cpu,
        "ap_cpu": ap_cpu,
        "py_cpu": py_cpu
    }
    
    with open("resource_monitor.jsonl", "a") as f:
        f.write(json.dumps(log) + "\n")

