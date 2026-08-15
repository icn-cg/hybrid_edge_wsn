import pytest

from gateway.aggregator import WindowAggregator
from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus
from gateway.server import ReceivedMessage


def received(
    node_id: str,
    sequence: int,
    *,
    monotonic_ns: int,
    temperature: float,
    humidity: float,
    pressure: float,
) -> ReceivedMessage:
    return ReceivedMessage(
        message=ReadingMessage(
            type="reading",
            version=1,
            node_id=node_id,
            node_kind="virtual",
            sequence=sequence,
            timestamp_ms=1_000 + sequence,
            temperature_c=temperature,
            humidity_pct=humidity,
            pressure_hpa=pressure,
        ),
        received_at_ms=2_000 + monotonic_ns // 1_000_000,
        received_monotonic_ns=monotonic_ns,
        wire_bytes=180,
        sequence_status=SequenceStatus.IN_ORDER,
    )


def test_known_inputs_produce_expected_summary() -> None:
    aggregator = WindowAggregator(5.0)
    readings = [
        received(
            "virtual-001",
            0,
            monotonic_ns=1_000_000_000,
            temperature=20,
            humidity=40,
            pressure=1000,
        ),
        received(
            "virtual-002",
            0,
            monotonic_ns=2_000_000_000,
            temperature=24,
            humidity=50,
            pressure=1010,
        ),
        received(
            "virtual-001",
            1,
            monotonic_ns=3_000_000_000,
            temperature=22,
            humidity=60,
            pressure=1020,
        ),
    ]
    for item in readings:
        assert aggregator.add(item) is None

    summary = aggregator.flush(partial_window=True, window_end_ms=4_500)
    assert summary is not None
    assert summary.reading_count == 3
    assert summary.node_count == 2
    assert summary.temperature_mean_c == pytest.approx(22)
    assert summary.temperature_min_c == 20
    assert summary.temperature_max_c == 24
    assert summary.humidity_mean_pct == pytest.approx(50)
    assert summary.humidity_min_pct == 40
    assert summary.humidity_max_pct == 60
    assert summary.pressure_mean_hpa == pytest.approx(1010)
    assert summary.pressure_min_hpa == 1000
    assert summary.pressure_max_hpa == 1020
    assert summary.partial_window is True
    assert summary.window_end_ms == 4_500


def test_reading_at_boundary_completes_previous_window() -> None:
    aggregator = WindowAggregator(1.0)
    first = received(
        "virtual-001",
        0,
        monotonic_ns=5_000_000_000,
        temperature=20,
        humidity=40,
        pressure=1000,
    )
    boundary = received(
        "virtual-002",
        0,
        monotonic_ns=6_000_000_000,
        temperature=30,
        humidity=50,
        pressure=1010,
    )

    assert aggregator.add(first) is None
    completed = aggregator.add(boundary)
    assert completed is not None
    assert completed.reading_count == 1
    assert completed.temperature_mean_c == 20
    assert completed.partial_window is False

    current = aggregator.flush(partial_window=True)
    assert current is not None
    assert current.reading_count == 1
    assert current.temperature_mean_c == 30


def test_invalid_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        WindowAggregator(0)
