"""Conservative search interruption checkpoint and resume selection."""

from __future__ import annotations

from .models import SearchResumeCheckpoint, SearchResumePolicyName


class SearchResumeError(ValueError):
    pass


class SearchResumeManager:
    def __init__(self, search_first_seq: int, rtl_seq: int, policy: SearchResumePolicyName):
        if search_first_seq < 1 or rtl_seq <= search_first_seq:
            raise ValueError("Invalid searchable mission sequence range")
        self.search_first_seq = search_first_seq
        self.rtl_seq = rtl_seq
        self.policy = policy

    def checkpoint(
        self, *, mission_id: str, mission_fingerprint: str, current_seq: int,
        reached_items: list[int], east_m: float, north_m: float,
        coverage_percent: float, current_search_segment: str | None = None,
    ) -> SearchResumeCheckpoint:
        if not self.search_first_seq <= current_seq < self.rtl_seq:
            raise SearchResumeError("Checkpoint may only be saved during a search mission item")
        reached_search = [seq for seq in reached_items if self.search_first_seq <= seq < self.rtl_seq]
        last_reached = max(reached_search, default=self.search_first_seq)
        return SearchResumeCheckpoint(
            mission_id=mission_id, mission_fingerprint=mission_fingerprint,
            current_mission_seq=current_seq, last_reached_mission_seq=last_reached,
            current_search_segment=current_search_segment,
            vehicle_east_m=east_m, vehicle_north_m=north_m,
            coverage_percent=coverage_percent,
        )

    def select_resume_sequence(self, checkpoint: SearchResumeCheckpoint) -> int:
        if self.policy is SearchResumePolicyName.REPLAY_FROM_LAST_REACHED:
            candidate = checkpoint.last_reached_mission_seq
        else:
            if checkpoint.current_mission_seq > checkpoint.last_reached_mission_seq:
                candidate = checkpoint.current_mission_seq
            else:
                candidate = min(self.rtl_seq - 1, checkpoint.current_mission_seq + 1)
        if not self.search_first_seq <= candidate < self.rtl_seq:
            raise SearchResumeError("Resume would replay TAKEOFF, a non-search command, or RTL")
        return candidate
