from gateway.protocol import HeartbeatMessage, ReadingMessage
from gateway.registry import HealthStatus, NodeRegistry, SequenceStatus


def reading(node_id: str, sequence: int) -> ReadingMessage:
    return ReadingMessage(
        type="reading",
        version=1,
        node_id=node_id,
        node_kind="virtual",
        sequence=sequence,
        timestamp_ms=1_000 + sequence,
        temperature_c=20.0 + sequence,
        humidity_pct=50.0,
        pressure_hpa=1013.0,
    )


def heartbeat(node_id: str, sequence: int) -> HeartbeatMessage:
    return HeartbeatMessage(
        type="heartbeat",
        version=1,
        node_id=node_id,
        node_kind="virtual",
        sequence=sequence,
        timestamp_ms=1_000 + sequence,
    )


def observe(registry: NodeRegistry, sequence: int, monotonic_ns: int | None = None):
    timestamp_ns = sequence * 1_000_000 if monotonic_ns is None else monotonic_ns
    return registry.observe(
        reading("virtual-001", sequence),
        received_at_ms=2_000 + sequence,
        received_monotonic_ns=timestamp_ns,
    )


def test_registration_and_normal_sequence_progression() -> None:
    registry = NodeRegistry()

    assert observe(registry, 5) is SequenceStatus.FIRST
    assert observe(registry, 6) is SequenceStatus.IN_ORDER

    record = registry.nodes["virtual-001"]
    assert record.first_seen_ms == 2_005
    assert record.last_seen_ms == 2_006
    assert record.last_sequence == 6
    assert record.messages_received == 2
    assert record.latest_sensor_values == {
        "temperature_c": 26.0,
        "humidity_pct": 50.0,
        "pressure_hpa": 1013.0,
    }
    assert record.health_status is HealthStatus.ONLINE


def test_duplicate_gap_and_out_of_order_classification() -> None:
    registry = NodeRegistry()
    statuses = [
        observe(registry, 10),
        observe(registry, 10),
        observe(registry, 13),
        observe(registry, 12),
    ]

    assert statuses == [
        SequenceStatus.FIRST,
        SequenceStatus.DUPLICATE,
        SequenceStatus.GAP,
        SequenceStatus.OUT_OF_ORDER,
    ]
    record = registry.nodes["virtual-001"]
    assert record.duplicates == 1
    assert record.estimated_messages_missing == 2
    assert record.out_of_order == 1
    assert record.last_sequence == 13


def test_heartbeat_updates_liveness_without_erasing_latest_sensor_values() -> None:
    registry = NodeRegistry()
    observe(registry, 0, monotonic_ns=0)
    status = registry.observe(
        heartbeat("virtual-001", 1),
        received_at_ms=2_100,
        received_monotonic_ns=100_000_000,
    )

    assert status is SequenceStatus.IN_ORDER
    assert registry.nodes["virtual-001"].latest_sensor_values is not None


def test_connection_count_handles_duplicate_node_sessions() -> None:
    registry = NodeRegistry()
    observe(registry, 0)

    registry.connection_opened("virtual-001")
    registry.connection_opened("virtual-001")
    assert registry.nodes["virtual-001"].connected is True
    assert registry.nodes["virtual-001"].active_connections == 2

    registry.connection_closed("virtual-001")
    assert registry.nodes["virtual-001"].connected is True
    registry.connection_closed("virtual-001")
    assert registry.nodes["virtual-001"].connected is False


def test_online_suspect_offline_online_and_sequence_reset() -> None:
    registry = NodeRegistry(
        expected_interval_seconds=1.0,
        suspect_after_intervals=3,
        offline_after_intervals=5,
    )
    assert observe(registry, 20, monotonic_ns=1_000_000_000) is SequenceStatus.FIRST

    assert registry.refresh_health(
        now_monotonic_ns=4_000_000_000, now_ms=4_000
    )[0].current is HealthStatus.SUSPECT
    assert registry.refresh_health(
        now_monotonic_ns=6_000_000_000, now_ms=6_000
    )[0].current is HealthStatus.OFFLINE

    reset = registry.observe(
        reading("virtual-001", 0),
        received_at_ms=6_100,
        received_monotonic_ns=6_100_000_000,
    )
    record = registry.nodes["virtual-001"]
    assert reset is SequenceStatus.RESET
    assert record.health_status is HealthStatus.ONLINE
    assert record.sequence_resets == 1
    assert [transition.current for transition in record.transitions] == [
        HealthStatus.ONLINE,
        HealthStatus.SUSPECT,
        HealthStatus.OFFLINE,
        HealthStatus.ONLINE,
    ]


def test_health_threshold_configuration_is_validated() -> None:
    try:
        NodeRegistry(suspect_after_intervals=5, offline_after_intervals=5)
    except ValueError as exc:
        assert "offline threshold" in str(exc)
    else:
        raise AssertionError("invalid thresholds were accepted")
