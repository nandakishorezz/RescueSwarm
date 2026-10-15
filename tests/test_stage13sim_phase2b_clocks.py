from __future__ import annotations

import pytest

from rescue_core.geolocation.clock_sync import (
    AffineClockMapping, ClockDomain, ClockSyncEstimator, Timestamp,
)
from rescue_core.simulation.perception.clock import SimulationClockMapper


def test_timestamp_domains_cannot_be_subtracted_or_implicitly_coerced():
    with pytest.raises(TypeError, match="domain"):
        Timestamp(10, ClockDomain.GAZEBO_SIM) - Timestamp(10, ClockDomain.HOST_MONOTONIC)
    with pytest.raises(ValueError):
        Timestamp(10.0, ClockDomain.GAZEBO_SIM)
    with pytest.raises(TypeError):
        Timestamp(10, "GAZEBO_SIM")
    assert Timestamp(20, ClockDomain.GAZEBO_SIM) - Timestamp(10, ClockDomain.GAZEBO_SIM) == 10


@pytest.mark.parametrize("slope", [.18, .5, 1, 2, 5.5])
def test_affine_mapping_handles_rtf_without_epoch_precision_loss(slope):
    fit = AffineClockMapping(ClockDomain.GAZEBO_SIM, ClockDomain.HOST_MONOTONIC)
    base = 10**17
    for index in range(30):
        source = 10**15 + index * 30_000_000
        host = base + round(index * 30_000_000 * slope)
        fit.observe(Timestamp(source, fit.source), Timestamp(host, fit.destination), host_received_ns=host)
    snapshot = fit.snapshot(host)
    assert snapshot["quality"] == "VALID"
    assert snapshot["slope"] == pytest.approx(slope)
    value, _ = fit.map(Timestamp(source, fit.source), host)
    assert abs(value.value_ns - host) <= 10
    assert len(fit.samples) <= 256


def test_mapping_reset_stale_and_wrong_domain_fail_closed():
    fit = AffineClockMapping(ClockDomain.GAZEBO_SIM, ClockDomain.HOST_MONOTONIC)
    for index in range(30):
        source = index * 100_000_000
        host = 10**12 + source
        fit.observe(Timestamp(source, fit.source), Timestamp(host, fit.destination), host_received_ns=host)
    assert fit.snapshot(host + 2_000_000_000)["quality"] == "INVALID"
    with pytest.raises(TypeError):
        fit.map(Timestamp(source, ClockDomain.ARDUPILOT_BOOT), host)
    fit.observe(Timestamp(0, fit.source), Timestamp(host + 1, fit.destination), host_received_ns=host + 1)
    assert fit.epoch == 1
    assert fit.snapshot(host + 1)["quality"] == "CONVERGING"


def test_affine_timesync_rejects_rtt_but_accepts_changing_offset_at_low_rtf():
    estimator = ClockSyncEstimator(minimum_samples=12, affine=True)
    for index in range(30):
        sent = 10**12 + index * 100_000_000
        remote = 10**9 + index * 20_000_000
        assert estimator.observe_response(sent, remote, sent + 2_000_000)
    fit = estimator.affine.snapshot(sent + 2_000_000)
    assert fit["quality"] == "VALID"
    assert fit["slope"] == pytest.approx(.2)
    assert not estimator.observe_response(sent + 100_000_000, remote + 20_000_000, sent + 200_000_000)
    estimator.observe_response(sent + 300_000_000, 100, sent + 302_000_000)
    assert estimator.affine.epoch == 1
    assert len(estimator.samples) == 1


def test_clock_windows_are_bounded_for_ten_minutes_simulation():
    fit = AffineClockMapping(ClockDomain.GAZEBO_SIM, ClockDomain.HOST_MONOTONIC)
    for index in range(18001):
        source = index * 33_333_333
        host = 10**12 + source * 5
        fit.observe(Timestamp(source, fit.source), Timestamp(host, fit.destination), host_received_ns=host)
    assert len(fit.samples) <= 31
    assert fit.snapshot(host)["quality"] == "VALID"


def test_combined_fit_never_certifies_unknown_one_way_bias():
    sync = ClockSyncEstimator(minimum_samples=12, affine=True)
    mapper = SimulationClockMapper("run", sync)
    for index in range(60):
        host = 10**12 + index * 100_000_000
        remote = 10**9 + index * 20_000_000
        sync.observe_response(host - 1_000_000, remote, host + 1_000_000)
        mapper.observe_clock(remote, host, 1_000_000)
        mapper.observe_boot(remote, host + 1_000_000)
    result = mapper.snapshot(host + 1_000_000)
    assert result["autonomy_mapping_permitted"] is False
    assert result["one_way_transport_bias_bound_ns"] is None
    mapper.observe_boot(1, host + 2_000_000)
    assert mapper.boot_epoch == 1
