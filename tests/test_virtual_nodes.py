import asyncio

from gateway.protocol import SensorMessage
from gateway.server import GatewayServer, ReceivedMessage
from virtual_nodes.node import VirtualNode, VirtualNodeConfig


async def test_ten_concurrent_virtual_nodes_reliably_send_readings() -> None:
    received: list[SensorMessage] = []
    all_received = asyncio.Event()

    def collect(item: ReceivedMessage) -> None:
        received.append(item.message)
        if len(received) == 30:
            all_received.set()

    gateway = GatewayServer(port=0, on_message=collect)
    await gateway.start()
    nodes = [
        VirtualNode(
            VirtualNodeConfig(
                node_id=f"virtual-{index:03d}",
                port=gateway.bound_port,
                sampling_interval=0.005,
                seed=662 + index,
            )
        )
        for index in range(10)
    ]

    try:
        await asyncio.wait_for(
            asyncio.gather(*(node.run(max_samples=3) for node in nodes)), timeout=2.0
        )
        await asyncio.wait_for(all_received.wait(), timeout=1.0)
    finally:
        await gateway.stop()

    assert gateway.stats.connections_accepted == 10
    assert gateway.stats.valid_messages == 30
    assert gateway.stats.invalid_messages == 0
    assert {message.node_id for message in received} == {
        f"virtual-{index:03d}" for index in range(10)
    }
    for node in nodes:
        assert node.samples_generated == 3
        assert node.messages_sent == 3
        assert node.sequence == 3


async def test_dropped_application_message_creates_sequence_gap() -> None:
    node = VirtualNode(VirtualNodeConfig(node_id="virtual-drop", drop_probability=1.0))

    # Data generation still advances sequence numbers when application-level sending is suppressed.
    first = node.make_reading()
    second = node.make_reading()

    assert first.sequence == 0
    assert second.sequence == 1
    assert node.samples_generated == 2
    assert first.node_kind == "virtual"


async def test_virtual_node_reconnects_when_gateway_becomes_available() -> None:
    # Reserve an ephemeral port, close it, and let the node encounter connection refusal first.
    probe = GatewayServer(port=0)
    await probe.start()
    port = probe.bound_port
    await probe.stop()

    received: list[SensorMessage] = []
    node = VirtualNode(
        VirtualNodeConfig(
            node_id="virtual-reconnect",
            port=port,
            sampling_interval=0.005,
            reconnect_initial=0.01,
            reconnect_max=0.02,
        )
    )
    node_task = asyncio.create_task(node.run(max_samples=2))
    gateway: GatewayServer | None = None
    try:
        await asyncio.sleep(0.03)
        gateway = GatewayServer(port=port, on_message=lambda item: received.append(item.message))
        await gateway.start()
        await asyncio.wait_for(node_task, timeout=1.0)

        async def await_messages() -> None:
            while len(received) < 2:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(await_messages(), timeout=1.0)
    finally:
        if not node_task.done():
            node_task.cancel()
            await asyncio.gather(node_task, return_exceptions=True)
        if gateway is not None:
            await gateway.stop()

    assert [message.sequence for message in received] == [0, 1]
    assert node.messages_sent == 2
