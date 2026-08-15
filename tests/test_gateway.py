import asyncio
import json

import pytest

from gateway.protocol import SensorMessage
from gateway.server import GatewayServer, ReceivedMessage


def reading_line(node_id: str, sequence: int) -> bytes:
    return (
        json.dumps(
            {
                "type": "reading",
                "version": 1,
                "node_id": node_id,
                "node_kind": "virtual",
                "sequence": sequence,
                "timestamp_ms": 1_786_482_001_123 + sequence,
                "temperature_c": 22.1,
                "humidity_pct": 48.2,
                "pressure_hpa": 1013.2,
            }
        ).encode()
        + b"\n"
    )


@pytest.fixture
async def running_gateway():
    received: list[SensorMessage] = []
    gateway = GatewayServer(port=0, on_message=lambda item: received.append(item.message))
    await gateway.start()
    try:
        yield gateway, received
    finally:
        await gateway.stop()


async def test_stream_framing_handles_split_and_coalesced_messages(running_gateway) -> None:
    gateway, received = running_gateway
    _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
    first = reading_line("virtual-001", 0)
    second = reading_line("virtual-001", 1)
    third = reading_line("virtual-001", 2)

    split_at = len(first) // 2
    writer.write(first[:split_at])
    await writer.drain()
    writer.write(first[split_at:] + second + third)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

    await wait_until(lambda: len(received) == 3)
    assert [message.sequence for message in received] == [0, 1, 2]
    assert gateway.stats.valid_messages == 3


async def test_bad_message_does_not_poison_connection_or_server(running_gateway) -> None:
    gateway, received = running_gateway
    _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
    writer.write(b"not json\n" + reading_line("virtual-good", 0))
    await writer.drain()
    writer.close()
    await writer.wait_closed()

    await wait_until(lambda: len(received) == 1)
    assert received[0].node_id == "virtual-good"
    assert gateway.stats.invalid_messages == 1


async def test_abrupt_disconnect_with_partial_message_is_safe(running_gateway) -> None:
    gateway, _received = running_gateway
    _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
    writer.write(b'{"type":"reading"')
    await writer.drain()
    writer.close()
    await writer.wait_closed()

    await wait_until(lambda: gateway.stats.active_connections == 0)
    assert gateway.stats.invalid_messages == 1


async def test_overlong_message_is_rejected_without_crashing_listener() -> None:
    received: list[SensorMessage] = []
    gateway = GatewayServer(
        port=0,
        max_message_bytes=512,
        on_message=lambda item: received.append(item.message),
    )
    await gateway.start()
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(b"x" * 600 + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await wait_until(lambda: gateway.stats.invalid_messages == 1)

        # A new well-behaved client is still accepted by the same listener.
        _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(reading_line("virtual-good", 0))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await wait_until(lambda: len(received) == 1)
    finally:
        await gateway.stop()


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_gateway_records_receive_time_and_wire_size() -> None:
    received: list[ReceivedMessage] = []
    gateway = GatewayServer(port=0, on_message=received.append)
    await gateway.start()
    line = reading_line("virtual-timed", 0)
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(line)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await wait_until(lambda: len(received) == 1)
    finally:
        await gateway.stop()

    assert received[0].wire_bytes == len(line)
    assert received[0].received_at_ms >= received[0].message.timestamp_ms
    assert received[0].received_monotonic_ns > 0
