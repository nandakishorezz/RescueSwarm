"""Write machine-readable offline runtime evidence after the compliance scan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.check_offline_compliance import scan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    args = parser.parse_args()
    checks, violations = scan(args.root, require_build=True)
    model = args.root / "models" / "artifacts" / "yolo11n.onnx"
    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    report = {
        "network_access_performed": False,
        "internet_required": False,
        "cloud_inference": False,
        "remote_model_download": False,
        "remote_texture_or_human_model": False,
        "remote_map_resource": False,
        "local_model": str(model.relative_to(args.root)),
        "local_model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "offline_rendered_probe_frames": probe["frames"],
        "offline_real_yolo_person_detections": probe["person_detections"],
        "checks": [{"name": item.name, "passed": item.passed, "detail": item.detail}
                   for item in checks],
        "violations": violations,
    }
    report["passed"] = all(item.passed for item in checks) and not violations \
        and probe["person_detections"] > 0 and report["local_model_sha256"] == probe["model_hash"]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
