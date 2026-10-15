import json
import os
import glob

def main():
    runs = ["phase4_payload_microtest_7", "phase4_payload_microtest_8", "phase4_payload_microtest_9"]
    results = []
    
    for r in runs:
        summary_path = f"data/simulation_runs/{r}/PHASE4_GOLD_SUMMARY.json"
        if not os.path.exists(summary_path):
            print(f"Missing {summary_path}")
            results.append({"run": r, "status": "FAIL_MISSING_SUMMARY"})
            continue
            
        with open(summary_path) as f:
            summary = json.load(f)
            
        truth_path = f"data/simulation_runs/{r}/SIMULATION_ONLY_GROUND_TRUTH_payload_slot_1.jsonl"
        with open(truth_path) as f:
            lines = f.readlines()
            last_line = json.loads(lines[-1])
            z_end = last_line["vehicle_position_gazebo_m"][2]
            
        results.append({
            "run": r,
            "impact_error_m": summary.get("impact_error_m"),
            "physical_delivery_score": summary.get("physical_delivery_score"),
            "payload_z_end": z_end,
            "pass": summary.get("physical_delivery_score") == "PASS" and z_end <= 0.25
        })
        
    out = {
        "tests": results,
        "overall_pass": all(r.get("pass") for r in results)
    }
    
    with open("PHASE4_PAYLOAD_DETACH_MICROTEST.json", "w") as f:
        json.dump(out, f, indent=2)
        
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
