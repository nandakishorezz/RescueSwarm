"""Hard safety barrier for non-flight physical bench tools."""

from __future__ import annotations

from typing import Any


class PhysicalBenchSafetyError(RuntimeError):
    """Raised for every actuator, mode, or navigation operation on the bench."""


class PhysicalBenchSafetyGuard:
    """An explicit deny-all flight-command surface for Stage 6.

    This is independent of ArduPilot pre-arm checks and therefore remains effective even
    when firmware would otherwise accept a command.
    """

    stage = 6
    read_only = True
    propellers_required_removed = True

    def require_propellers_removed(self, confirmed: bool) -> None:
        if not confirmed:
            raise PhysicalBenchSafetyError(
                "REMOVE PROPELLERS and explicitly confirm before Pixhawk bench procedures"
            )

    def reject(self, operation: str) -> None:
        raise PhysicalBenchSafetyError(
            f"{operation} is forbidden in Stage 6 physical bench mode; flight controller access is read-only"
        )

    def arm(self, *_: Any, **__: Any) -> None: self.reject("arm")
    def takeoff(self, *_: Any, **__: Any) -> None: self.reject("takeoff")
    def set_mode(self, *_: Any, **__: Any) -> None: self.reject("set_mode")
    def upload_mission(self, *_: Any, **__: Any) -> None: self.reject("upload_mission")
    def start_mission(self, *_: Any, **__: Any) -> None: self.reject("start_mission")
    def goto(self, *_: Any, **__: Any) -> None: self.reject("goto")
    def rtl(self, *_: Any, **__: Any) -> None: self.reject("rtl")
    def land(self, *_: Any, **__: Any) -> None: self.reject("land")
    def servo(self, *_: Any, **__: Any) -> None: self.reject("servo")
    def motor_test(self, *_: Any, **__: Any) -> None: self.reject("motor_test")
    def release_payload(self, *_: Any, **__: Any) -> None: self.reject("release_payload")
