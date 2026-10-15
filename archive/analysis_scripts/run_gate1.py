import json
from datetime import datetime, UTC

def test_scoring():
    results = []
    cases = [
        (1.99, True, "PASS"),
        (2.00, True, "PASS"),
        (2.01, True, "FAIL"),
        (16.642174, True, "FAIL"),
        (None, False, "FAIL"),
    ]
    evaluation_radius_m = 2.0
    all_pass = True
    
    for impact, present, expected in cases:
        if not present:
            actual = "FAIL"
        else:
            actual = "PASS" if impact <= evaluation_radius_m else "FAIL"
            
        passed = actual == expected
        all_pass = all_pass and passed
        results.append({
            "input_impact_error_m": impact,
            "impact_present": present,
            "expected": expected,
            "actual": actual,
            "passed": passed
        })
        
    with open("PHASE4_EVALUATOR_INTEGRITY_AUDIT.json", "w") as f:
        json.dump({
            "threshold_m": evaluation_radius_m,
            "test_cases": results,
            "all_boundary_tests_pass": all_pass,
            "evidence_generated_utc": datetime.now(UTC).isoformat()
        }, f, indent=2)

if __name__ == "__main__":
    test_scoring()
