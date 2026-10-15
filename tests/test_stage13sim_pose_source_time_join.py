"""Unit tests for the pose source-time sidecar join (Phase-2C final).

Covers the SimulationPoseTimestampWitness wire-identity join, its join
metrics, the AP-boot-ms identity field, and the schema guard that enforces
the no-world-state hard rule for sidecar records.
"""

import time

import pytest

from rescue_core.simulation.perception.clock import (
    SimulationPoseTimestampWitness,
    SimulationPoseTimestampWitnessRecord,
    _guard_witness_record,
)


def _record(msgid: int, seq: int, ap_boot_ms: int, ap_sim_ns: int) -> SimulationPoseTimestampWitnessRecord:
    return SimulationPoseTimestampWitnessRecord(
        run_id="ut", msgid=msgid, chan=0, seq=seq, ap_boot_ms=ap_boot_ms,
        sysid=1, compid=1, ap_sim_ns=ap_sim_ns,
        host_received_ns=time.monotonic_ns(),
    )


def test_guard_rejects_world_state_fields() -> None:
    with pytest.raises(ValueError, match="SCHEMA_VIOLATION"):
        _guard_witness_record({"kind": "pose_src_time", "latitude_microdeg": 1.0,
                               "altitude": 50.0})


def test_guard_allows_identity_and_time_only() -> None:
    _guard_witness_record({
        "kind": "pose_src_time", "msgid": 33, "chan": 0, "seq": 5,
        "ap_boot_ms": 4819, "sysid": 1, "compid": 1,
        "ap_sim_us": 4819000, "ap_sim_fdm_us": 4820000,
    })


def test_record_carries_ap_boot_ms_identity() -> None:
    rec = _record(33, 4, ap_boot_ms=4819, ap_sim_ns=4_819_000_000)
    d = rec.as_dict()
    assert d["ap_boot_ms"] == 4819
    assert "latitude" not in d and "attitude" not in d


def test_exact_wire_identity_pairs_rows_to_messages() -> None:
    w = SimulationPoseTimestampWitness("ut")
    for i in range(3):
        w.observe(_record(33, i, 1000 + i, (1000 + i) * 1_000_000))
    # Arrival order cannot select the timestamp: only the MAVLink header seq can.
    assert w.join(0, 1, 1, 33, 2, 1002).ap_sim_ns == 1_002_000_000
    assert w.join(0, 1, 1, 33, 0, 1000).ap_sim_ns == 1_000_000_000
    assert w.join(0, 1, 1, 33, 1, 1001).ap_sim_ns == 1_001_000_000
    assert w.join(0, 1, 1, 33, 9, 1009) is None
    metrics = w.snapshot()["join_metrics"]
    assert metrics["joined"] == 3
    assert metrics["missed"] == 1


def test_queues_are_family_local() -> None:
    w = SimulationPoseTimestampWitness("ut")
    w.observe(_record(30, 0, 1000, 1_000_000_000))
    w.observe(_record(33, 0, 1001, 1_001_000_000))
    assert w.join(0, 1, 1, 33, 0, 1001).ap_sim_ns == 1_001_000_000
    assert w.join(0, 1, 1, 30, 0, 1000).ap_sim_ns == 1_000_000_000


def test_join_wait_absorb_transport_interleaving() -> None:
    w = SimulationPoseTimestampWitness("ut", max_join_wait_ns=50_000_000)
    w.observe(_record(33, 0, 1000, 1_000_000_000))  # row already there
    assert w.join(0, 1, 1, 33, 0, 1000).ap_sim_ns == 1_000_000_000
    # Message arrives before its sidecar row: join waits for the row.
    def _late_row() -> None:
        time.sleep(0.01)
        w.observe(_record(31, 1, 1010, 1_010_000_000))
    import threading
    t = threading.Thread(target=_late_row)
    t.start()
    assert w.join(0, 1, 1, 31, 1, 1010).ap_sim_ns == 1_010_000_000
    t.join()


def test_family_rows_and_snapshot_shape() -> None:
    w = SimulationPoseTimestampWitness("ut")
    w.observe(_record(30, 0, 1000, 1_000_000_000))
    w.observe(_record(30, 1, 1010, 1_010_000_000))
    pending = w.family_rows(0, 1, 1, 30)
    assert [p["seq"] for p in pending] == [0, 1]
    snap = w.snapshot()
    assert snap["tablesize"] == 2
    assert snap["no_world_state"] is True
