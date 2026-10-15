"""Simulation clock mapping and direct sim clock witness.

Phase-2C direct mapping (SITL_GAZEBO):
    Gazebo _info.simTime -> JSON "timestamp" -> SIM_JSON time_now_us
    -> ARDUPILOT_SIM_SOURCE -> PoseHistory

Camera gz.msgs.Image.header.stamp shares the same Gazebo sim clock epoch.
Direct mapping T_ap = a*T_gazebo + b is measured from source-domain pairs.
Host wall-clock is NOT used as the authoritative capture->pose bridge.

Phase-2B legacy (SimulationClockMapper) retained for compatibility only.
It cannot qualify gold because of unknown host one-way transport bias.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Optional

from rescue_core.geolocation.clock_sync import (
    AffineClockMapping, ClockDomain, ClockSyncEstimator, ClockSyncQuality,
    MappingQuality, Timestamp,
)


class SimulationClockMapper:
    def __init__(self, run_id: str, timesync: ClockSyncEstimator):
        if timesync.affine is None:
            raise ValueError("Simulation requires opt-in affine TIMESYNC")
        self.run_id = run_id
        self.timesync = timesync
        self.gazebo_to_host = AffineClockMapping(
            ClockDomain.GAZEBO_SIM, ClockDomain.HOST_MONOTONIC,
            max_error_ns=100_000_000,
        )
        self.bridge_to_host = AffineClockMapping(
            ClockDomain.BRIDGE_MONOTONIC, ClockDomain.HOST_MONOTONIC,
        )
        self.boot_to_timesync = AffineClockMapping(
            ClockDomain.ARDUPILOT_BOOT, ClockDomain.ARDUPILOT_TIMESYNC,
        )
        self.last_sim_ns: int | None = None
        self.last_boot_ns: int | None = None
        self.boot_epoch = 0

    def observe_clock(self, sim_ns: int, host_receive_ns: int, transport_bound_ns: int) -> None:
        if self.last_sim_ns is not None and sim_ns < self.last_sim_ns:
            self.gazebo_to_host.invalidate("GAZEBO_RESET")
            self.boot_to_timesync.invalidate("GAZEBO_RESET")
        elif self.last_sim_ns is not None and sim_ns - self.last_sim_ns < 10_000_000:
            # Gazebo publishes at physics rate (1 kHz); fit time coverage, not
            # hundreds of almost simultaneous points that erase the window.
            return
        self.last_sim_ns = sim_ns
        self.gazebo_to_host.observe(
            Timestamp(sim_ns, ClockDomain.GAZEBO_SIM),
            Timestamp(host_receive_ns, ClockDomain.HOST_MONOTONIC),
            host_received_ns=host_receive_ns, uncertainty_ns=transport_bound_ns,
        )

    def observe_boot(self, boot_ns: int, host_receive_ns: int) -> None:
        if self.last_boot_ns is not None and boot_ns < self.last_boot_ns:
            self.boot_epoch += 1
            self.boot_to_timesync.invalidate("ARDUPILOT_BOOT_RESET")
            self.timesync.affine.invalidate("ARDUPILOT_BOOT_RESET")
        self.last_boot_ns = boot_ns
        try:
            remote, mapping = self.timesync.affine.map(
                Timestamp(host_receive_ns, ClockDomain.HOST_MONOTONIC), host_receive_ns,
            )
        except ValueError:
            return
        self.boot_to_timesync.observe(
            Timestamp(boot_ns, ClockDomain.ARDUPILOT_BOOT), remote,
            host_received_ns=host_receive_ns, uncertainty_ns=int(mapping["uncertainty_ns"]),
        )

    def snapshot(self, now_ns: int) -> dict[str, object]:
        gazebo = self.gazebo_to_host.snapshot(now_ns)
        ap = self.timesync.affine.snapshot(now_ns)
        boot = self.boot_to_timesync.snapshot(now_ns)
        result: dict[str, object] = {
            "simulation_run_id": self.run_id, "autopilot_boot_epoch": self.boot_epoch,
            "created_host_monotonic_ns": now_ns,
            "gazebo_to_host": gazebo, "host_to_autopilot_timesync": ap,
            "autopilot_boot_to_timesync": boot,
            "bridge_to_host": self.bridge_to_host.snapshot(now_ns),
            "quality": MappingQuality.CONVERGING.value,
            "autonomy_mapping_permitted": False,
            "one_way_transport_bias_bound_ns": None,
            "independent_motion_validation": "NOT_PERFORMED",
            "capture_timestamp_authority": "gz.msgs.Image.header.stamp",
            "receipt_time_is_capture_time": False,
        }
        if all("slope" in item for item in (gazebo, ap, boot)):
            slope = float(gazebo["slope"]) * float(ap["slope"]) / float(boot["slope"])
            offset = (float(ap["slope"]) * float(gazebo["offset_ns"]) + float(ap["offset_ns"]) - float(boot["offset_ns"])) / float(boot["slope"])
            residual = (float(gazebo["uncertainty_ns"]) * abs(float(ap["slope"])) + float(ap["uncertainty_ns"]) + float(boot["uncertainty_ns"])) / abs(float(boot["slope"]))
            result["gazebo_to_autopilot_boot"] = {
                "slope": slope, "offset_ns": offset,
                "fit_uncertainty_ns": residual,
                "effective_error_bound_ns": None,
                "warning": "Fit residual excludes unknown Gazebo and telemetry one-way delay bias",
            }
            result["quality"] = MappingQuality.DEGRADED.value
        if any(item["quality"] == MappingQuality.INVALID.value for item in (gazebo, ap, boot)):
            result["quality"] = MappingQuality.INVALID.value
        return result

    def map_gazebo_to_autopilot(self, sim_ns: int, now_host_ns: int) -> tuple[int, dict[str, object]]:
        """Return a mapped AP boot timestamp only after the full gate is valid."""
        snapshot = self.snapshot(now_host_ns)
        combined = snapshot.get("gazebo_to_autopilot_boot")
        if snapshot.get("quality") != MappingQuality.VALID.value or not isinstance(combined, dict):
            raise ValueError(f"CLOCK_MAPPING_INVALID: {snapshot.get('quality')}")
        bound = combined.get("effective_error_bound_ns")
        if bound is None:
            raise ValueError("CLOCK_MAPPING_INVALID: one-way transport bias unbounded")
        if float(bound) > 16_700_000:
            raise ValueError("CLOCK_MAPPING_INVALID: effective error exceeds frame half-period")
        return round(float(combined["slope"]) * sim_ns + float(combined["offset_ns"])), snapshot


# ---------------------------------------------------------------------------
# Phase-2C: Direct simulation clock witness (SITL_GAZEBO only)
# ---------------------------------------------------------------------------

# Sentinel: a row exists for this message identity family but with a different
# seq. join() retries briefly rather than pairing the wrong seq.
_JOIN_WAIT_MORE = object()

_WITNESS_FORBIDDEN_FIELDS = frozenset([
    "position", "velocity", "roll", "pitch", "yaw", "quaternion",
    "attitude", "pose", "location", "survivor", "target", "bbox",
    "detection", "segmentation", "truth", "world_pos", "lat", "lon", "altitude",
])


def _guard_witness_record(record: dict) -> None:
    """Raise if any world-state field appears in a witness record."""
    for k in record:
        normalized = k.lower()
        if (normalized in _WITNESS_FORBIDDEN_FIELDS
                or any(normalized.startswith(field + "_")
                       for field in _WITNESS_FORBIDDEN_FIELDS)):
            raise ValueError(
                f"SCHEMA_VIOLATION: forbidden field {k!r} in witness record"
            )


@dataclass(frozen=True, slots=True)
class DirectSimClockWitnessRecord:
    """Single timing sample: (Gazebo sim ns, AP boot ns). No world state.

    Both clocks share the Gazebo simulation epoch under lockstep.
    host_received_ns is wall-clock at observation, used for window eviction only.
    """
    run_id: str
    sequence: int
    gazebo_sim_ns: int        # GAZEBO_SIM domain, ns
    ap_boot_ns: int           # compatibility name; ARDUPILOT_SIM_SOURCE domain, ns
    host_received_ns: int     # HOST_MONOTONIC, ns (eviction only)

    def as_dict(self) -> dict:
        d = asdict(self)
        _guard_witness_record(d)
        return d


class DirectSimClockWitness:
    """Phase-2C: collects (Gazebo sim ns, AP boot ns) pairs for direct fitting.

    Threading: thread-safe via internal lock.
    Memory: bounded deque of max_samples records.

    observe_source_pair  -- call for each same-update source-domain pair
        emitted by the SIM_JSON clock witness patch. BOTH timestamps come from
        the same FDM update inside SIM_JSON::recv_fdm (state.timestamp_s and
        time_now_us). There is NO host-arrival latching and NO MAVLink
        telemetry coupling: the pair is authoritative by construction.

    Under lockstep (no_time_sync=0, lock_step=1) both clocks advance in
    simulation time together. Host receipt time is used ONLY for window
    eviction and staleness detection (AffineClockMapping), never to form the
    pair. The witness contains NO pose, attitude, velocity, or world-state
    fields.
    """

    def __init__(
        self,
        run_id: str,
        *,
        max_samples: int = 600,
        window_ns: int = 120_000_000_000,   # 120 s host window
        min_samples: int = 30,
        min_span_ns: int = 5_000_000_000,   # 5 s Gazebo sim coverage
        max_error_ns: int = 260_000_000,    # 260 ms = one MAVLink 4 Hz interval + margin
        max_age_ns: int | None = None,      # None → use window_ns
    ) -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self._records: deque = deque(maxlen=max_samples)
        # max_age_ns: how stale the most recent sample may be before the mapping
        # is declared INVALID. Must be >= window_ns to survive RTL/landing phase
        # when the camera stops but the runner keeps waiting for disarm.
        _max_age = max_age_ns if max_age_ns is not None else window_ns
        self._mapping = AffineClockMapping(
            ClockDomain.GAZEBO_SIM, ClockDomain.ARDUPILOT_SIM_SOURCE,
            max_samples=max_samples, window_ns=window_ns,
            min_samples=min_samples, min_span_ns=min_span_ns,
            max_error_ns=max_error_ns, max_age_ns=_max_age,
        )

    def observe_source_pair(
        self, gazebo_sim_ns: int, ap_boot_ns: int, host_received_ns: int
    ) -> "DirectSimClockWitnessRecord":
        """Append a same-update source-domain pair.

        gazebo_sim_ns and ap_boot_ns MUST originate from the same FDM update
        inside SIM_JSON::recv_fdm (the clock witness patch). host_received_ns
        is the receipt wall-clock, used for window eviction only.
        """
        if gazebo_sim_ns < 0 or ap_boot_ns < 0:
            raise ValueError("DIRECT_SIM_CLOCK_INVALID: negative witness timestamp")
        with self._lock:
            self._seq += 1
            rec = DirectSimClockWitnessRecord(
                run_id=self.run_id, sequence=self._seq,
                gazebo_sim_ns=gazebo_sim_ns, ap_boot_ns=ap_boot_ns,
                host_received_ns=host_received_ns,
            )
            self._records.append(rec)
            self._mapping.observe(
                Timestamp(gazebo_sim_ns, ClockDomain.GAZEBO_SIM),
                Timestamp(ap_boot_ns, ClockDomain.ARDUPILOT_SIM_SOURCE),
                host_received_ns=host_received_ns,
                uncertainty_ns=0,   # Same-update source-domain pair: no transport bias
            )
        return rec

    def quality(self) -> MappingQuality:
        snap = self._mapping.snapshot(time.monotonic_ns())
        return MappingQuality(snap.get("quality", MappingQuality.UNINITIALIZED.value))

    def map_gazebo_to_ap(self, gazebo_sim_ns: int) -> "tuple[int, dict]":
        """Map Gazebo capture ns to AP boot ns. Raises ValueError if not VALID."""
        snap = self._mapping.snapshot(time.monotonic_ns())
        q = snap.get("quality")
        if q != MappingQuality.VALID.value:
            raise ValueError(f"DIRECT_SIM_CLOCK_INVALID: quality={q}")
        ap_ns = int(snap["destination_anchor_ns"]) + round(
            float(snap["slope"]) * (gazebo_sim_ns - int(snap["source_anchor_ns"]))
            + float(snap["intercept_at_anchor_ns"])
        )
        if ap_ns < 0:
            raise ValueError("DIRECT_SIM_CLOCK_INVALID: negative mapped AP time")
        return ap_ns, snap

    def snapshot(self) -> dict:
        """Full diagnostic snapshot. Contains only timing fields."""
        now = time.monotonic_ns()
        m = self._mapping.snapshot(now)
        with self._lock:
            n = len(self._records)
            recent = [r.as_dict() for r in list(self._records)[-5:]]
        return {
            "witness_class": "DirectSimClockWitness",
            "run_id": self.run_id,
            "sample_count": n,
            "mapping": m,
            "quality": m.get("quality", MappingQuality.UNINITIALIZED.value),
            "autonomy_mapping_permitted": m.get("quality") == MappingQuality.VALID.value,
            "clock_chain": "GAZEBO_SIM -> ARDUPILOT_SIM_SOURCE (direct, no host transport)",
            "no_world_state": True,
            "recent_samples": recent,
        }


class DirectSimCaptureClockMapper:
    """Maps Gazebo camera capture timestamps to AP boot time. SITL_GAZEBO only.

    Fail-closed: map_gazebo_to_autopilot raises ValueError unless quality=VALID.
    Drop-in for SimulationClockMapper.map_gazebo_to_autopilot signature.
    """

    EXECUTION_SOURCE = "SITL_GAZEBO"

    def __init__(self, witness: DirectSimClockWitness) -> None:
        self.witness = witness

    def quality(self) -> MappingQuality:
        return self.witness.quality()

    def sync_quality(self) -> ClockSyncQuality:
        """Quality token for PoseHistory.lookup_autopilot_time."""
        return (
            ClockSyncQuality.SYNCED
            if self.witness.quality() is MappingQuality.VALID
            else ClockSyncQuality.UNSYNCED
        )

    def map_gazebo_to_autopilot(self, sim_ns: int, _now_ns: int) -> "tuple[int, dict]":
        """Map Gazebo sim ns to AP boot ns. Raises if not VALID.

        _now_ns accepted for API compatibility with SimulationClockMapper
        but is not used as evidence.
        """
        q = self.witness.quality()
        if q is not MappingQuality.VALID:
            raise ValueError(f"DIRECT_SIM_CLOCK_INVALID: {q.value}")
        return self.witness.map_gazebo_to_ap(sim_ns)

    def snapshot(self, _now_ns: int = 0) -> dict:
        return self.witness.snapshot()


class PhysicalCaptureClockMapper:
    """Stub for physical H1 hardware clock strategy (not Phase 2)."""

    EXECUTION_SOURCE = "PHYSICAL_HARDWARE"

    def quality(self) -> MappingQuality:
        return MappingQuality.UNINITIALIZED

    def map_gazebo_to_autopilot(self, sim_ns: int, _now_ns: int) -> "tuple[int, dict]":
        raise NotImplementedError("PhysicalCaptureClockMapper not Phase-2")


# ---------------------------------------------------------------------------
# Phase-2C final: MAVLink pose source-time sidecar (SITL_GAZEBO only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SimulationPoseTimestampWitnessRecord:
    """Sidecar row joining a MAVLink pose message to ARDUPILOT_SIM_SOURCE time.

    Emitted by GCS_Common.cpp at the exact pose-message serialization points
    (send_global_position_int / send_attitude / send_attitude_quaternion) and
    forwarded to the host over the same bridge stream as the pose telemetry.
    Holds identity + the ArduPilot simulation clock ONLY (sitl->state.timestamp_us
    == SIM loop time_now_us, a lockstep-gazebo-synchronised epoch). It carries
    NO pose/position/attitude/world-state fields.
    """
    run_id: str
    msgid: int
    chan: int
    seq: int
    ap_boot_ms: int          # GCS-thread millis stamped into the message's time_boot_ms field
    sysid: int
    compid: int
    ap_sim_ns: int            # ARDUPILOT_SIM_SOURCE, ns
    host_received_ns: int     # HOST_MONOTONIC, ns (eviction only)

    def as_dict(self) -> dict:
        d = asdict(self)
        _guard_witness_record(d)
        return d


@dataclass(frozen=True, slots=True)
class SimulationPoseTimestampWitnessJoin:
    """Outcome of joining a received MAVLink pose message to its sidecar row."""
    joined: bool
    key: tuple
    reason: str
    counter: int


class SimulationPoseTimestampWitness:
    """Binds MAVLink pose telemetry back to ArduPilot simulation source time.

    The pose sidecar rows ride the SAME single bridge stream as the pose
    MAVLink messages (TCP lines), so this witness adds no second consumer of
    the SITL MAVLink socket and no competing recv_match().

    PoseHistory samples produced from these joins are keyed in the
    ARDUPILOT_SIM_SOURCE domain -- the same domain
    DirectSimClockWitness.map_gazebo_to_ap returns -- so frame capture time
    (gazebo) -> DirectSimCaptureClockMapper -> PoseHistory.lookup_autopilot_time
    is a strict source-time lookup with no host-receipt-time and no
    wall-fallback boot clock in the authoritative path.

    Joined by the exact outgoing MAVLink identity captured immediately before
    serialization: (channel, sysid, compid, msgid, MAVLink sequence,
    message time_boot_ms). The boot field is used only as immutable message
    identity, never as the source-time key. Including it disambiguates the
    8-bit MAVLink sequence after wrap while a fast-simulation transmit backlog
    is in flight. Host arrival order is deliberately not part of the match.
    """

    def __init__(
        self,
        run_id: str,
        *,
        ttl_ns: int = 600_000_000_000,
        max_entries: int = 200_000,
        max_join_wait_ns: int = 50_000_000,
    ) -> None:
        self.run_id = run_id
        self.ttl_ns = ttl_ns
        self.max_entries = max_entries
        self.max_join_wait_ns = max_join_wait_ns
        self._lock = threading.Lock()
        self._records: dict[tuple, SimulationPoseTimestampWitnessRecord] = {}
        self._last_sequence: dict[tuple, int] = {}
        self._counters = {
            "received": 0, "duplicates": 0, "out_of_order": 0, "expired": 0,
            "joined": 0, "missed": 0, "late_for_join": 0,
        }

    def observe(self, record: SimulationPoseTimestampWitnessRecord) -> None:
        """Reader side: insert a sidecar row forwarded from the bridge stream."""
        family = (record.chan, record.sysid, record.compid, record.msgid)
        key = (*family, record.seq & 0xFF, record.ap_boot_ms)
        with self._lock:
            now = time.monotonic_ns()
            self._evict_locked(now)
            previous = self._last_sequence.get(family)
            if previous is not None and ((record.seq - previous) & 0xFF) not in range(1, 128):
                self._counters["out_of_order"] += 1
            self._last_sequence[family] = record.seq & 0xFF
            if key in self._records:
                self._counters["duplicates"] += 1
                return
            if len(self._records) >= self.max_entries:
                oldest = min(self._records, key=lambda k: self._records[k].host_received_ns)
                self._records.pop(oldest)
                self._counters["expired"] += 1
            self._records[key] = record
            self._counters["received"] += 1

    def _total_rows_locked(self) -> int:
        return len(self._records)

    def join(
        self, chan: int, sysid: int, compid: int, msgid: int, seq: int,
        ap_boot_ms: int,
        *, max_wait_ns: int | None = None,
    ) -> "SimulationPoseTimestampWitnessRecord | None":
        """Consume the sidecar row whose identity equals the wire message."""
        wait_ns = max_wait_ns if max_wait_ns is not None else self.max_join_wait_ns
        deadline = time.monotonic_ns() + wait_ns
        while True:
            now = time.monotonic_ns()
            rec = self._consume(chan, sysid, compid, msgid, seq, ap_boot_ms, now)
            if rec is not None:
                return rec
            if now >= deadline:
                break
            time.sleep(0.0002)
        with self._lock:
            self._counters["missed"] += 1
        return None

    def _consume(
        self, chan: int, sysid: int, compid: int, msgid: int, seq: int,
        ap_boot_ms: int, now_ns: int
    ) -> "SimulationPoseTimestampWitnessRecord | None":
        with self._lock:
            self._evict_locked(now_ns)
            rec = self._records.pop(
                (chan, sysid, compid, msgid, seq & 0xFF, ap_boot_ms), None)
            if rec is None:
                return None
            self._counters["joined"] += 1
            return rec

    def _evict_locked(self, now_ns: int) -> None:
        threshold = now_ns - self.ttl_ns
        expired = [key for key, row in self._records.items()
                   if row.host_received_ns < threshold]
        for key in expired:
            self._records.pop(key, None)
            self._counters["expired"] += 1

    def family_rows(self, chan: int, sysid: int, compid: int, msgid: int) -> list[dict]:
        """Return pending rows for diagnostics, sorted by source time."""
        with self._lock:
            rows = [row for key, row in self._records.items()
                    if key[:4] == (chan, sysid, compid, msgid)]
            rows.sort(key=lambda row: row.ap_sim_ns)
            return [{"seq": r.seq, "ap_boot_ms": r.ap_boot_ms} for r in rows]

    def snapshot(self) -> dict:
        with self._lock:
            self._evict_locked(time.monotonic_ns())
            n = self._total_rows_locked()
            recent = []
            recent.extend(r.as_dict() for r in self._records.values())
            recent = recent[-5:]
            counters = dict(self._counters)
        joined = counters["joined"]
        total = joined + counters["missed"]
        recv_total = counters["received"] + counters["duplicates"]
        return {
            "witness_class": "SimulationPoseTimestampWitness",
            "run_id": self.run_id,
            "tablesize": n,
            "siderecord_total": recv_total,
            "join_metrics": {
                "joined": joined,
                "missed": counters["missed"],
                "join_rate_pct": round(100.0 * joined / total, 2) if total else None,
                "duplicates": counters["duplicates"],
                "out_of_order": counters["out_of_order"],
                "expired_before_join": counters["expired"],
            },
            "no_world_state": True,
            "recent_records": recent,
        }

    def reset_metrics(self) -> None:
        """Reset qualification counters without discarding pending identities."""
        with self._lock:
            for key in self._counters:
                self._counters[key] = 0

    def join_attempt(self, attempt: SimulationPoseTimestampWitnessJoin) -> None:
        """Record an explicit join attempt outcome (diagnostic)."""
        with self._lock:
            self._counters.setdefault("join_attempts", 0)
            self._counters["join_attempts"] += 1
