"""Transport-neutral GUIDED navigator contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .approach import ApproachPoint


class GuidedNavigator(ABC):
    @abstractmethod
    def enter_guided(self) -> None: ...

    @abstractmethod
    def goto_local(self, point: ApproachPoint) -> None: ...

    @abstractmethod
    def set_velocity_local(self, east_m_s: float, north_m_s: float, down_m_s: float = 0) -> None: ...

    @abstractmethod
    def hold(self) -> None: ...

    @abstractmethod
    def set_mission_current(self, sequence: int) -> None: ...

    @abstractmethod
    def resume_auto(self) -> None: ...
