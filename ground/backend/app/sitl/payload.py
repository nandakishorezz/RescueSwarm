"""SITL actuator-to-Gazebo payload release bridge.

The mission side sees only actuator/mechanism acknowledgement. Impact scoring
is deliberately absent and remains in the simulation evaluator.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rescue_core.autonomy.models import DeliveryEvidence, PayloadSlot, PayloadState
from rescue_core.autonomy.payload import PayloadError, PayloadInterface

from .adapter import SITLAutopilotAdapter


class GazeboPayloadAdapter(PayloadInterface):
    def __init__(
        self, autopilot: SITLAutopilotAdapter, run_dir: Path, *,
        servo_number: int = 9, release_pwm: int = 1900,
    ) -> None:
        self.autopilot = autopilot
        self.run_dir = run_dir
        self.servo_number = servo_number
        self.release_pwm = release_pwm
        self.released_slots: set[str] = set()
        self.release_records: list[dict[str, object]] = []
        
        # Start persistent helper
        helper_path = "/mnt/c/Users/nanda/Downloads/SURVIVOR_DETECTION/ground/backend/app/sitl/payload_helper.py"
        self.wsl_proc = subprocess.Popen(
            ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-c",
             "export GZ_VERSION=harmonic;"
             "export GZ_SIM_SYSTEM_PLUGIN_PATH=/home/nandakishore7/ardupilot_gazebo/build;"
             f"python3 {helper_path}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1, # Line buffered
        )
        # Wait for READY
        ready = self.wsl_proc.stdout.readline().strip()
        if ready != "READY":
            raise RuntimeError("Payload helper failed to start")
            
        # Prewarm slots 1-3
        for i in range(1, 4):
            topic = f"/rescueswarm/payload/slot_{i}/detach"
            self.wsl_proc.stdin.write(f"PREWARM {topic}\n")
            self.wsl_proc.stdin.flush()
            resp = self.wsl_proc.stdout.readline().strip()  # PREWARMED or PREWARMED_NO_CONN
            if not resp or resp.startswith("ERROR"):
                raise RuntimeError(f"Payload helper failed during prewarm slot {i}: {resp!r}")

    def release(self, slot: PayloadSlot, **evidence: float) -> DeliveryEvidence:
        if slot.state is not PayloadState.RESERVED or not slot.target_id:
            raise PayloadError("Payload must be reserved before release")
        if slot.slot_id in self.released_slots:
            raise PayloadError("Payload slot release is exactly-once")
        slot.state = PayloadState.RELEASING
        requested = datetime.now(UTC)
        command_id = str(uuid4())
        self.autopilot.command_servo(self.servo_number + int(slot.slot_id.rsplit("_", 1)[-1]) - 1, self.release_pwm)
        
        topic = f"/rescueswarm/payload/{slot.slot_id.replace('_', '_')}/detach"
        # Guard against helper process death between prewarm and release
        if self.wsl_proc.poll() is not None:
            raise PayloadError(f"Payload helper process died (exit={self.wsl_proc.returncode}) before release")
        try:
            self.wsl_proc.stdin.write(f"{topic}\n")
            self.wsl_proc.stdin.flush()
        except OSError as exc:
            raise PayloadError(f"Payload helper stdin broken: {exc}") from exc
        
        # Read PUBLISHED or ERROR
        response = self.wsl_proc.stdout.readline().strip()
        if not response:
            self.wsl_proc.poll()
            raise PayloadError(f"Helper process died with code {self.wsl_proc.returncode}")
            
        if response.startswith("ERROR"):
            slot.state = PayloadState.FAILED
            raise PayloadError(f"Gazebo payload publish failed: {response}")
            
        transport_publish_success = response.startswith("PUBLISHED")
        
        # Read SUCCESS or FAILED
        response = self.wsl_proc.stdout.readline().strip()
        if not response:
            self.wsl_proc.poll()
            raise PayloadError(f"Helper process died while waiting for ACK, code {self.wsl_proc.returncode}")
            
        mechanism_ack = response.startswith("SUCCESS")
        
        if not mechanism_ack:
            slot.state = PayloadState.FAILED
            raise PayloadError(f"Gazebo payload detach failed: {response}")
            
        self.released_slots.add(slot.slot_id)
        slot.state = PayloadState.RELEASED
        completed = datetime.now(UTC)
        delivery = DeliveryEvidence(
            target_id=slot.target_id, payload_slot=slot.slot_id,
            execution_source="SITL_MAVLINK_SERVO_TO_GAZEBO_DETACHABLE_JOINT",
            release_requested_at=requested, release_completed_at=completed,
            vehicle_east_m=evidence["vehicle_east_m"], vehicle_north_m=evidence["vehicle_north_m"],
            relative_target_error_m=evidence["relative_target_error_m"],
            altitude_m=evidence["altitude_m"], ground_speed_m_s=evidence["ground_speed_m_s"],
            target_uncertainty_m=evidence["target_uncertainty_m"],
            release_succeeded=mechanism_ack, release_command_id=command_id,
            release_sim_time_ns=int(evidence.get("release_sim_time_ns", 0)),
            actuator_acknowledged=True, mechanism_acknowledged=mechanism_ack,
        )
        self.release_records.append({
            "command_id": command_id, "slot_id": slot.slot_id, "target_id": slot.target_id,
            "servo_number": self.servo_number, "release_pwm": self.release_pwm,
            "detach_topic": topic, "requested_at": requested.isoformat(),
            "completed_at": completed.isoformat(), "host_monotonic_ns": time.monotonic_ns(),
            "autonomy_physical_delivery_score_known": False,
        })
        return delivery

    def verify(self, slot: PayloadSlot, delivery: DeliveryEvidence) -> bool:
        acknowledged = bool(
            slot.state is PayloadState.RELEASED and delivery.release_succeeded
            and delivery.actuator_acknowledged and delivery.mechanism_acknowledged
        )
        delivery.verification_at = datetime.now(UTC)
        delivery.verification_succeeded = acknowledged
        slot.state = PayloadState.VERIFIED if acknowledged else PayloadState.UNKNOWN
        return acknowledged
