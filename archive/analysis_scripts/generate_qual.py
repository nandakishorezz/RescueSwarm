import json
import sys

def check_evidence():
    entered_revalidation = False
    passed_final_gate = False
    
    try:
        with open("PHASE4_MISSION_TIMELINE.jsonl", "r") as f:
            for line in f:
                data = json.loads(line)
                if data.get("event") in ["REACQUIRED", "PIXEL_COARSE_CORRECTION"]:
                    entered_revalidation = True
    except Exception as e:
        print(f"Error reading timeline: {e}")
                
    try:
        with open("DELIVERY_GATE_EVIDENCE.jsonl", "r") as f:
            lines = f.readlines()
            if lines:
                last_line = json.loads(lines[-1])
                passed_final_gate = last_line.get("all_passed", False)
    except Exception as e:
        print(f"Error reading evidence: {e}")
            
    out = {
        "fresh_revalidation_state_entered": "YES" if entered_revalidation else "NO",
        "final_gate_evidence_passed": passed_final_gate,
        "stale_coasting_repaired": entered_revalidation and passed_final_gate,
        "passed": entered_revalidation and passed_final_gate
    }
    
    with open("PHASE4_ALIGNMENT_REVALIDATION_QUALIFICATION.json", "w") as f:
        json.dump(out, f, indent=2)
        
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    check_evidence()
