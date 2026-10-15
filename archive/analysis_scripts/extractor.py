
import json
from pathlib import Path

run_dir = Path(r"c:\Users\nanda\Downloads\SURVIVOR_DETECTION\data\simulation_runs\phase4_alignment_reacq_final_1_retry1")

with open("vision_status_tmp.json") as f:
    vision = json.load(f)

rendered = vision["frames_captured"]
detector = vision["frames_processed"]
detections = vision["human_detections"]

print(f"rendered frames: {rendered}")
print(f"detector frames: {detector}")
print(f"person detections: {detections}")

confs = []
target_confirmed = False
prog_max = 0

if (run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl").exists():
    with open(run_dir / "TARGET_SERVICE_TRAJECTORY.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if "confidence" in d and d["confidence"] is not None:
                confs.append(d["confidence"])
            if "bbox_confidence" in d and d["bbox_confidence"] is not None:
                confs.append(d["bbox_confidence"])
            if "confirmation_progress" in d:
                prog_max = max(prog_max, d["confirmation_progress"])

if (run_dir / "PHASE4_MISSION_TIMELINE.jsonl").exists():
    with open(run_dir / "PHASE4_MISSION_TIMELINE.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d.get("event") == "TARGET_ASSIGNED":
                target_confirmed = True

if confs:
    print(f"confidence min / mean / max: {min(confs):.3f} / {sum(confs)/len(confs):.3f} / {max(confs):.3f}")
else:
    print("confidence min / mean / max: N/A")

print(f"maximum confirmation progress: {prog_max}")
print(f"target confirmed: {target_confirmed}")

approach_reached = False
reacq_started = False
reacquired = False
if target_confirmed:
    with open(run_dir / "PHASE4_MISSION_TIMELINE.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d.get("event") == "APPROACH_ARRIVAL": approach_reached = True
            if d.get("event") == "REACQUIRED": reacquired = True
            if d.get("event") == "APPROACH_START": reacq_started = True

print(f"approach reached: {approach_reached}")
print(f"reacq started: {reacq_started}")
print(f"reacquired: {reacquired}")

iterator_exhausted = 0
iterator_restarted = 0
search_points_cmd = 0
search_points_early = 0
initial_err = None
best_err = 9999
align_start = None
align_end = None
aligned = False
timeout_trig = False

if (run_dir / "ALIGNMENT_EVIDENCE.jsonl").exists():
    with open(run_dir / "ALIGNMENT_EVIDENCE.jsonl") as f:
        for line in f:
            d = json.loads(line)
            err = d.get("horizontal_error_m")
            if err is not None:
                if initial_err is None:
                    initial_err = err
                if err < best_err:
                    best_err = err
            
            if d.get("gate_ready") == True:
                aligned = True
            
            if d.get("iterator_exhausted"): iterator_exhausted += 1
            if d.get("iterator_restarted"): iterator_restarted += 1
            if "search_point_commanded" in d: search_points_cmd += 1
            if d.get("search_point_reached_early"): search_points_early += 1
            
            t = d.get("capture_monotonic_ns")
            if t is not None:
                if align_start is None: align_start = t
                align_end = t

print(f"initial err: {initial_err}")
print(f"best err: {best_err}")
print(f"aligned: {aligned}")
print(f"iterator_exhausted: {iterator_exhausted}")
print(f"iterator_restarted: {iterator_restarted}")
print(f"search_points_cmd: {search_points_cmd}")
print(f"search_points_early: {search_points_early}")
if align_start and align_end:
    print(f"duration: {(align_end - align_start)/1e9:.3f}s")
else:
    print("duration: N/A")

gate = False
detach = False
impact = False
impact_err = None
if (run_dir / "DELIVERY_GATE_EVIDENCE.jsonl").exists():
    with open(run_dir / "DELIVERY_GATE_EVIDENCE.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d.get("event") == "EVALUATE":
                if d.get("passed") or d.get("delivery_gate_passed"):
                    gate = True
            if d.get("event") == "PAYLOAD_DETACH": detach = True
            if d.get("event") == "PAYLOAD_IMPACT":
                impact = True
                impact_err = d.get("impact_error_m")

print(f"delivery gate: {gate}")
print(f"detach: {detach}")
print(f"impact: {impact}")
print(f"impact err: {impact_err}")

