import asyncio
import json
import time
from pathlib import Path

from collector.server import CollectedRecord, CollectorServer
from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus
from gateway.server import GatewayServer, ReceivedMessage
from gateway.upstream import ForwardMode, UpstreamForwarder
from gateway.upstream_protocol import AggregateUpstreamRecord, RawUpstreamRecord
from virtual_nodes.node import VirtualNode, VirtualNodeConfig

BASE_MONOTONIC_NS = time.monotonic_ns()


def received(node_id: str, sequence: int) -> ReceivedMessage:
    return ReceivedMessage(
        message=ReadingMessage(
            type="reading",
            version=1,
            node_id=node_id,
            node_kind="virtual",
            sequence=sequence,
            timestamp_ms=1_000 + sequence,
            temperature_c=20.0 + sequence,
            humidity_pct=40.0 + sequence,
            pressure_hpa=1000.0 + sequence,
        ),
        received_at_ms=2_000 + sequence,
        received_monotonic_ns=BASE_MONOTONIC_NS + sequence,
        wire_bytes=180,
        sequence_status=(SequenceStatus.FIRST if sequence == 0 else SequenceStatus.IN_ORDER),
    )


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_raw_mode_forwards_each_reading_and_counts_exact_bytes() -> None:
    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=0, on_record=collected.append)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1", collector.bound_port, mode=ForwardMode.RAW
    )
    await forwarder.start()
    try:
        for sequence in range(3):
            await forwarder.submit(received("virtual-raw", sequence))
        await forwarder.stop()
        await wait_until(lambda: len(collected) == 3)
    finally:
        await collector.stop()

    assert all(isinstance(item.record, RawUpstreamRecord) for item in collected)
    assert [item.record.reading.sequence for item in collected] == [0, 1, 2]
    assert forwarder.stats.upstream_messages == 3
    assert forwarder.stats.upstream_bytes == collector.stats.bytes_received


async def test_aggregated_mode_flushes_summary_with_known_statistics() -> None:
    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=0, on_record=collected.append)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        collector.bound_port,
        mode=ForwardMode.AGGREGATED,
        aggregation_window_seconds=5,
    )
    await forwarder.start()
    try:
        await forwarder.submit(received("virtual-001", 0))
        await forwarder.submit(received("virtual-002", 1))
        await forwarder.stop()
        await wait_until(lambda: len(collected) == 1)
    finally:
        await collector.stop()

    record = collected[0].record
    assert isinstance(record, AggregateUpstreamRecord)
    assert record.reading_count == 2
    assert record.node_count == 2
    assert record.temperature_mean_c == 20.5
    assert record.temperature_min_c == 20.0
    assert record.temperature_max_c == 21.0
    assert record.partial_window is True
    assert forwarder.stats.upstream_messages == 1


async def test_aggregated_mode_emits_when_full_window_expires() -> None:
    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=0, on_record=collected.append)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        collector.bound_port,
        mode=ForwardMode.AGGREGATED,
        aggregation_window_seconds=0.03,
    )
    await forwarder.start()
    item = received("virtual-window", 0)
    item = ReceivedMessage(
        message=item.message,
        received_at_ms=time.time_ns() // 1_000_000,
        received_monotonic_ns=time.monotonic_ns(),
        wire_bytes=item.wire_bytes,
        sequence_status=item.sequence_status,
    )
    try:
        await forwarder.submit(item)
        await wait_until(lambda: len(collected) == 1)
        assert isinstance(collected[0].record, AggregateUpstreamRecord)
        assert collected[0].record.partial_window is False
        await forwarder.stop()
    finally:
        await forwarder.stop()
        await collector.stop()


async def test_forwarder_reconnects_after_collector_becomes_available() -> None:
    probe = CollectorServer(port=0)
    await probe.start()
    port = probe.bound_port
    await probe.stop()

    forwarder = UpstreamForwarder(
        "127.0.0.1",
        port,
        mode=ForwardMode.RAW,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    await forwarder.start()
    await forwarder.submit(received("virtual-reconnect", 0))
    await wait_until(lambda: forwarder.stats.connection_failures >= 1)

    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=port, on_record=collected.append)
    await collector.start()
    try:
        await wait_until(lambda: len(collected) == 1)
        await forwarder.stop()
    finally:
        await collector.stop()

    assert collected[0].record.type == "raw"
    assert forwarder.stats.connection_attempts >= 2
    assert forwarder.stats.records_abandoned_on_shutdown == 0


async def test_unavailable_collector_has_bounded_shutdown() -> None:
    probe = CollectorServer(port=0)
    await probe.start()
    port = probe.bound_port
    await probe.stop()
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        port,
        mode=ForwardMode.RAW,
        reconnect_initial=0.01,
        reconnect_max=0.02,
        shutdown_timeout=0.05,
    )
    await forwarder.start()
    await forwarder.submit(received("virtual-unavailable", 0))

    await forwarder.stop()

    assert forwarder.stats.connection_failures >= 1
    assert forwarder.stats.records_abandoned_on_shutdown == 1


async def test_collector_persists_its_own_receive_metadata(tmp_path: Path) -> None:
    output = tmp_path / "upstream.ndjson"
    collector = CollectorServer(port=0, output_path=output)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1", collector.bound_port, mode=ForwardMode.RAW
    )
    await forwarder.start()
    try:
        await forwarder.submit(received("virtual-output", 0))
        await forwarder.stop()
        await wait_until(lambda: collector.stats.messages_received == 1)
    finally:
        await collector.stop()

    value = json.loads(output.read_text())
    assert value["record"]["type"] == "raw"
    assert value["record"]["reading"]["node_id"] == "virtual-output"
    assert value["collector_received_at_ms"] > 0
    assert value["upstream_wire_bytes"] == collector.stats.bytes_received


async def test_gateway_virtual_node_collector_raw_integration() -> None:
    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=0, on_record=collected.append)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1", collector.bound_port, mode=ForwardMode.RAW
    )
    await forwarder.start()
    gateway = GatewayServer(port=0, on_message=forwarder.submit)
    await gateway.start()
    node = VirtualNode(
        VirtualNodeConfig(
            node_id="virtual-e2e",
            port=gateway.bound_port,
            sampling_interval=0.005,
        )
    )
    try:
        await node.run(max_samples=3)
        await gateway.stop()
        await forwarder.stop()
        await wait_until(lambda: len(collected) == 3)
    finally:
        await gateway.stop()
        await forwarder.stop()
        await collector.stop()

    assert gateway.stats.valid_messages == 3
    assert collector.stats.messages_received == 3
    assert [item.record.reading.sequence for item in collected] == [0, 1, 2]


async def test_gateway_virtual_node_collector_aggregated_integration() -> None:
    collected: list[CollectedRecord] = []
    collector = CollectorServer(port=0, on_record=collected.append)
    await collector.start()
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        collector.bound_port,
        mode=ForwardMode.AGGREGATED,
        aggregation_window_seconds=1.0,
    )
    await forwarder.start()
    gateway = GatewayServer(port=0, on_message=forwarder.submit)
    await gateway.start()
    node = VirtualNode(
        VirtualNodeConfig(
            node_id="virtual-aggregate-e2e",
            port=gateway.bound_port,
            sampling_interval=0.005,
        )
    )
    try:
        await node.run(max_samples=4)
        await gateway.stop()
        await forwarder.stop()
        await wait_until(lambda: len(collected) == 1)
    finally:
        await gateway.stop()
        await forwarder.stop()
        await collector.stop()

    record = collected[0].record
    assert isinstance(record, AggregateUpstreamRecord)
    assert record.reading_count == 4
    assert record.node_count == 1
    assert forwarder.stats.readings_enqueued == 4
    assert forwarder.stats.upstream_messages == 1
    assert collector.stats.messages_received == 1
