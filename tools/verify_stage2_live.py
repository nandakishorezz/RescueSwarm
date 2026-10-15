#!/usr/bin/env python3
"""Verify a running local Stage 2 server, WebSocket, boundary, simulation, and review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from websockets.sync.client import connect


def verify(base: str, sample_boundary: Path) -> dict[str, object]:
    with httpx.Client(timeout=10) as client:
        root = client.get(f"{base}/")
        if root.status_code != 200 or "RescueSwarm Offline GCS" not in root.text:
            raise RuntimeError("Built frontend was not served by FastAPI")
        health = client.get(f"{base}/api/health").json()
        if health["status"] != "ok":
            raise RuntimeError(f"Backend health failed: {health}")
        mission = client.post(
            f"{base}/api/missions", json={"name": "Live Process Verification"}
        ).json()
        mission_id = mission["id"]
        boundary_response = client.post(
            f"{base}/api/missions/{mission_id}/boundary",
            files={
                "boundary_file": (
                    "boundary.geojson",
                    sample_boundary.read_bytes(),
                    "application/geo+json",
                )
            },
        )
        boundary_response.raise_for_status()
        boundary = boundary_response.json()["boundary"]
        validated = client.post(f"{base}/api/missions/{mission_id}/validate")
        validated.raise_for_status()
        websocket_base = base.replace("http:", "ws:").replace("https:", "wss:")
        with connect(f"{websocket_base}/ws/mission/{mission_id}") as socket:
            initial = json.loads(socket.recv())
            if initial["type"] != "system.health":
                raise RuntimeError(f"Unexpected initial WebSocket envelope: {initial}")
            client.post(f"{base}/api/engineering/simulator/{mission_id}/start").raise_for_status()
            telemetry = None
            for _ in range(10):
                message = json.loads(socket.recv())
                if message["type"] == "vehicle.telemetry":
                    telemetry = message
                    break
            if telemetry is None or telemetry["vehicle_id"] != "drone_01":
                raise RuntimeError("No normalized simulator telemetry received")
        client.post(f"{base}/api/engineering/simulator/{mission_id}/reset").raise_for_status()
        client.post(f"{base}/api/engineering/simulator/{mission_id}/complete").raise_for_status()
        review = client.get(f"{base}/api/missions/{mission_id}/review").json()
        if (
            review["mission"]["state"] != "COMPLETED"
            or review["targets_confirmed"] != 1
            or review["deliveries_completed"] != 1
        ):
            raise RuntimeError(f"Review verification failed: {review}")
        return {
            "static_frontend": "PASS",
            "health": health["status"],
            "boundary_area_m2": round(boundary["area_m2"], 2),
            "websocket": telemetry["type"],
            "sequence": telemetry["sequence"],
            "review_state": review["mission"]["state"],
            "targets": review["targets_found"],
            "deliveries": review["deliveries_completed"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--boundary", type=Path, default=Path("data/examples/sample_boundary.geojson")
    )
    args = parser.parse_args()
    print(json.dumps(verify(args.base.rstrip("/"), args.boundary), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

