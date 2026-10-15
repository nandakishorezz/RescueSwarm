import json

def check_vision(run_dir):
    print(f"Checking {run_dir}")
    path = f"{run_dir}/vision_events.jsonl"
    
    detections = 0
    frames = 0
    with open(path, 'r') as f:
        for line in f:
            data = json.loads(line)
            if data["type"] == "vision.detection":
                frames += 1
                if len(data["payload"]["detections"]) > 0:
                    detections += 1
                    
    print(f"Total frames: {frames}")
    print(f"Total detections: {detections}")

check_vision("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/phase4_alignment_qualification_1_retry28")
check_vision("C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/stage13sim_yolo_v3_live_search_smoke")
