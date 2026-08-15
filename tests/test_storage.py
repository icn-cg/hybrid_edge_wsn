import asyncio
import json
from pathlib import Path

import pytest

from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus
from gateway.server import GatewayServer, ReceivedMessage
from gateway.storage import RawMessageStore
from virtual_nodes.node import VirtualNode, VirtualNodeConfig


def received(sequence: int) -> ReceivedMessage:
    return ReceivedMessage(
        message=ReadingMessage(
            type="reading",
            version=1,
            node_id="virtual-storage",
            node_kind="virtual",
            sequence=sequence,
            timestamp_ms=1_000 + sequence,
            temperature_c=22.0,
            humidity_pct=48.0,
            pressure_hpa=1013.0,
        ),
        received_at_ms=2_000 + sequence,
        received_monotonic_ns=3_000 + sequence,
        wire_bytes=180,
        sequence_status=(SequenceStatus.FIRST if sequence == 0 else SequenceStatus.IN_ORDER),
    )


async def test_store_persists_gateway_metadata_and_drains_on_stop(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "readings.ndjson"
    store = RawMessageStore(output, queue_size=2, batch_size=2)
    await store.start()
    for sequence in range(3):
        await store.submit(received(sequence))
    await store.stop()

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["sequence"] for record in records] == [0, 1, 2]
    assert records[0]["gateway_received_at_ms"] == 2_000
    assert records[0]["sensor_wire_bytes"] == 180
    assert records[0]["sequence_status"] == "FIRST"
    assert store.stats.records_enqueued == 3
    assert store.stats.records_written == 3
    assert store.stats.bytes_written == output.stat().st_size


async def test_store_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    output = tmp_path / "readings.ndjson"
    output.write_text("preserve me\n")
    store = RawMessageStore(output)

    with pytest.raises(FileExistsError):
        await store.start()

    assert output.read_text() == "preserve me\n"


async def test_submit_requires_running_store(tmp_path: Path) -> None:
    store = RawMessageStore(tmp_path / "readings.ndjson")
    with pytest.raises(RuntimeError, match="not running"):
        await store.submit(received(0))


async def test_gateway_to_store_integration(tmp_path: Path) -> None:
    output = tmp_path / "gateway-readings.ndjson"
    store = RawMessageStore(output)
    await store.start()
    gateway = GatewayServer(port=0, on_message=store.submit)
    await gateway.start()
    node = VirtualNode(
        VirtualNodeConfig(
            node_id="virtual-persisted",
            port=gateway.bound_port,
            sampling_interval=0.005,
        )
    )
    try:
        await asyncio.wait_for(node.run(max_samples=3), timeout=1.0)
    finally:
        await gateway.stop()
        await store.stop()

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["sequence"] for record in records] == [0, 1, 2]
    assert all(record["node_id"] == "virtual-persisted" for record in records)


async def test_raw_store_keeps_writing_when_upstream_queue_is_full(tmp_path: Path) -> None:
    from collector.server import CollectorServer
    from gateway.upstream import ForwardMode, UpstreamForwarder

    output = tmp_path / "decoupled.ndjson"
    store = RawMessageStore(output)
    await store.start()
    probe = CollectorServer(port=0)
    await probe.start()
    port = probe.bound_port
    await probe.stop()
    forwarder = UpstreamForwarder(
        "127.0.0.1",
        port,
        mode=ForwardMode.RAW,
        queue_size=1,
        reconnect_initial=0.01,
        reconnect_max=0.02,
        shutdown_timeout=0.05,
    )
    await forwarder.start()
    try:
        for sequence in range(4):
            item = received(sequence)
            await store.submit(item)
            forwarder.try_submit(item)
    finally:
        await store.stop()
        await forwarder.stop()

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["sequence"] for record in records] == [0, 1, 2, 3]
    assert store.stats.records_written == 4
    assert forwarder.stats.queue_full_drops >= 1
