from pathlib import Path
from ground.backend.app.sitl.phase4_evaluator import Phase4QualificationEvaluator
import json

if __name__ == "__main__":
    run_dir = Path("data/simulation_runs/probe10")
    evaluator = Phase4QualificationEvaluator(run_dir)
    qual = evaluator.evaluate()
    with open(run_dir / "qualification.json", "w") as f:
        json.dump(qual.model_dump(), f, indent=2)
    print(f"Passed: {qual.passed}")
    for k, v in qual.metrics.items():
        if k.startswith("infrastructure"): continue
        print(f"{k}: {v}")
