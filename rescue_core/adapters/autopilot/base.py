"""Autopilot abstraction with Stage 1 write-operation safety barriers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Stage1SafetyError(RuntimeError):
    """Raised whenever code attempts an operation forbidden in Stage 1."""


class AutopilotAdapter(ABC):
    """Read-only Stage 1 interface for replaceable autopilot implementations."""

    @abstractmethod
    def connect(self) -> dict[str, Any]: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_heartbeat(self, timeout: float | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def get_vehicle_identity(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_firmware_version(self, timeout: float = 5.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_gps(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_global_position(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_attitude(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_heading(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_battery(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_system_status(self, timeout: float = 2.0) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_parameters_snapshot(
        self, timeout: float = 60.0, idle_timeout: float = 3.0
    ) -> dict[str, float]: ...

    @abstractmethod
    def get_capabilities(self) -> dict[str, Any]: ...

    # Future stages may replace these barriers with reviewed implementations.
    def _stage1_block(self, operation: str) -> None:
        raise Stage1SafetyError(f"{operation} is intentionally disabled in Stage 1")

    def arm(self, *_: Any, **__: Any) -> None:
        self._stage1_block("arm")

    def takeoff(self, *_: Any, **__: Any) -> None:
        self._stage1_block("takeoff")

    def upload_mission(self, *_: Any, **__: Any) -> None:
        self._stage1_block("upload_mission")

    def start_mission(self, *_: Any, **__: Any) -> None:
        self._stage1_block("start_mission")

    def pause(self, *_: Any, **__: Any) -> None:
        self._stage1_block("pause")

    def goto(self, *_: Any, **__: Any) -> None:
        self._stage1_block("goto")

    def set_velocity(self, *_: Any, **__: Any) -> None:
        self._stage1_block("set_velocity")

    def rtl(self, *_: Any, **__: Any) -> None:
        self._stage1_block("rtl")

    def land(self, *_: Any, **__: Any) -> None:
        self._stage1_block("land")

    def release_payload(self, *_: Any, **__: Any) -> None:
        self._stage1_block("release_payload")

