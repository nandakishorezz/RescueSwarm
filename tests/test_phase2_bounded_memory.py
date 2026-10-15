from rescue_core.simulation.perception.pipeline import BoundedHistory


def test_bounded_history_evicts_oldest_and_tracks_evictions():
    values = BoundedHistory(3)
    values.extend(range(10))
    assert values == [7, 8, 9]
    assert values.evicted == 7
    values.append(10)
    assert values == [8, 9, 10]
    assert values.evicted == 8

