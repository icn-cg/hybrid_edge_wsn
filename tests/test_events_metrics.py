import asyncio
import csv
import time
from pathlib import Path

from collector.server import CollectorServer
from gateway.events import ExperimentEventRecorder, GatewayEvent, HealthEvent
from gateway.metrics import SystemMetricsSampler
from gateway.protocol import ReadingMessage, encode_message
from gateway.registry import HealthStatus, NodeRegistry, SequenceStatus
from gateway.server import GatewayServer, ReceivedMessage
from gateway.upstream import ForwardMode, UpstreamForwarder


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout)


def received(sequence: int) -> ReceivedMessage:
    return ReceivedMessage(
        message=ReadingMessage(
            type="reading",
            version=1,
            node_id="virtual-events",
            node_kind="virtual",
            sequence=sequence,
            timestamp_ms=time.time_ns() // 1_000_000,
            temperature_c=22,
            humidity_pct=48,
            pressure_hpa=1013,
        ),
        received_at_ms=time.time_ns() // 1_000_000,
        received_monotonic_ns=time.monotonic_ns(),
        wire_bytes=180,
        sequence_status=(
            SequenceStatus.FIRST if sequence == 0 else SequenceStatus.IN_ORDER
        ),
    )


async def test_event_recorder_persists_gateway_and_health_rows(tmp_path: Path) -> None:
    recorder = ExperimentEventRecorder(
        tmp_path / "gateway_events.csv", tmp_path / "health_events.csv", queue_size=2
    )
    await recorder.start()
    recorder.record_gateway(
        GatewayEvent(1_000, "upstream", "queue_full_drop", "virtual-001", {"size": 2})
    )
    recorder.record_gateway(
        GatewayEvent(1_001, "upstream", "collector_reconnected")
    )
    recorder.record_health(
        HealthEvent(1_002, "virtual-001", "virtual", "ONLINE", "SUSPECT", "timeout", 900, 7)
    )
    await recorder.stop()

    with (tmp_path / "gateway_events.csv").open(newline="") as source:
        gateway_rows = list(csv.DictReader(source))
    with (tmp_path / "health_events.csv").open(newline="") as source:
        health_rows = list(csv.DictReader(source))
    assert gateway_rows[0]["event_type"] == "queue_full_drop"
    assert gateway_rows[0]["details_json"] == '{"size":2}'
    assert gateway_rows[1]["event_type"] == "collector_reconnected"
    assert health_rows[0]["new_state"] == "SUSPECT"
    assert recorder.gateway_writer.stats.written == 2
    assert recorder.health_writer.stats.written == 1


async def test_metrics_sampler_starts_stops_and_produces_rows(tmp_path: Path) -> None:
    output = tmp_path / "system_metrics.csv"
    sampler = SystemMetricsSampler(output, interval_seconds=0.01)
    await sampler.start()
    await asyncio.sleep(0.035)
    await sampler.stop()

    with output.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) >= 2
    assert int(rows[0]["process_rss_bytes"]) > 0
    assert sampler.stats.samples_written == len(rows)


async def test_real_health_transitions_are_persisted(tmp_path: Path) -> None:
    recorder = ExperimentEventRecorder(
        tmp_path / "gateway.csv", tmp_path / "health.csv"
    )
    registry = NodeRegistry(
        expected_interval_seconds=0.01,
        suspect_after_intervals=1,
        offline_after_intervals=2,
    )
    gateway = GatewayServer(
        port=0,
        registry=registry,
        liveness_check_interval=0.003,
        on_health_event=recorder.record_health,
    )
    await recorder.start()
    await gateway.start()
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(encode_message(received(0).message))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await wait_until(
            lambda: registry.nodes.get("virtual-events") is not None
            and registry.nodes["virtual-events"].health_status is HealthStatus.OFFLINE
        )
    finally:
        await gateway.stop()
        await recorder.stop()

    with (tmp_path / "health.csv").open(newline="") as source:
        states = [row["new_state"] for row in csv.DictReader(source)]
    assert states == ["ONLINE", "SUSPECT", "OFFLINE"]


async def test_forwarder_queue_full_and_reconnect_events_are_persisted(
    tmp_path: Path,
) -> None:
    probe = CollectorServer(port=0)
    await probe.start()
    port = probe.bound_port
    await probe.stop()
    recorder = ExperimentEventRecorder(
        tmp_path / "gateway.csv", tmp_path / "health.csv"
    )
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        port,
        mode=ForwardMode.RAW,
        queue_size=1,
        reconnect_initial=0.005,
        reconnect_max=0.01,
        on_event=recorder.record_gateway,
    )
    collector = CollectorServer(port=port)
    await recorder.start()
    await forwarder.start()
    try:
        assert forwarder.try_submit(received(0)) is True
        await wait_until(lambda: forwarder.stats.connection_failures >= 1)
        assert forwarder.try_submit(received(1)) is True
        assert forwarder.try_submit(received(2)) is False
        await collector.start()
        await wait_until(lambda: forwarder.stats.upstream_messages == 2)
    finally:
        await forwarder.stop()
        await collector.stop()
        await recorder.stop()

    with (tmp_path / "gateway.csv").open(newline="") as source:
        event_types = [row["event_type"] for row in csv.DictReader(source)]
    assert "collector_unavailable" in event_types
    assert "queue_full_drop" in event_types
    assert "collector_reconnected" in event_types
