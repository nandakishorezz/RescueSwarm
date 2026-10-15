import sys

paths = [
    "scripts/run_stage13sim_phase4.py",
    "scripts/run_stage13sim_phase2.py"
]

target = """        adapter.prepare_for_arm(); adapter.arm(); adapter.start_auto_mission()"""

replacement = """        gazebo_source.start()
        while gazebo_source.metrics.frames_received == 0:
            time.sleep(0.1)
        adapter.prepare_for_arm(); adapter.arm(); adapter.start_auto_mission()"""

for path in paths:
    with open(path, "r") as f:
        content = f.read()
    
    if target in content:
        content = content.replace(target, replacement)
        with open(path, "w") as f:
            f.write(content)
        print(f"Patched {path}")
    else:
        print(f"Target not found in {path}")
