"""Concurrent NDJSON/TCP gateway for physical and virtual sensor nodes."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from gateway.events import GatewayEvent, HealthEvent
from gateway.protocol import ProtocolError, SensorMessage, parse_message
from gateway.registry import HealthTransition, NodeRegistry, SequenceStatus

LOGGER = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8662
DEFAULT_MAX_MESSAGE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """A validated sensor message annotated at the gateway receive boundary."""

    message: SensorMessage
    received_at_ms: int
    received_monotonic_ns: int
    wire_bytes: int
    sequence_status: SequenceStatus


MessageHandler = Callable[[ReceivedMessage], Awaitable[None] | None]
GatewayEventHandler = Callable[[GatewayEvent], None]
HealthEventHandler = Callable[[HealthEvent], None]


@dataclass(slots=True)
class GatewayStats:
    """Application-boundary counters; these are not TCP/IP packet counters."""

    connections_accepted: int = 0
    active_connections: int = 0
    valid_messages: int = 0
    sensor_bytes: int = 0
    malformed_json: int = 0
    schema_rejections: int = 0
    rejected_readings: int = 0
    overlong_messages: int = 0
    truncated_messages: int = 0
    idle_disconnects: int = 0

    @property
    def invalid_messages(self) -> int:
        """Compatibility total; use the individual counters for analysis."""

        return (
            self.malformed_json
            + self.schema_rejections
            + self.overlong_messages
            + self.truncated_messages
        )


class GatewayServer:
    """An asyncio server that frames and validates newline-delimited JSON."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        client_idle_timeout: float = 30.0,
        registry: NodeRegistry | None = None,
        liveness_check_interval: float = 0.5,
        on_message: MessageHandler | None = None,
        on_gateway_event: GatewayEventHandler | None = None,
        on_health_event: HealthEventHandler | None = None,
    ) -> None:
        if max_message_bytes < 1:
            raise ValueError("max_message_bytes must be positive")
        if client_idle_timeout <= 0:
            raise ValueError("client_idle_timeout must be positive")
        if liveness_check_interval <= 0:
            raise ValueError("liveness_check_interval must be positive")

        self.host = host
        self.port = port
        self.max_message_bytes = max_message_bytes
        self.client_idle_timeout = client_idle_timeout
        self.liveness_check_interval = liveness_check_interval
        self.on_message = on_message
        self.on_gateway_event = on_gateway_event
        self.on_health_event = on_health_event
        self.stats = GatewayStats()
        self.registry = registry or NodeRegistry()
        self._server: asyncio.Server | None = None
        self._liveness_task: asyncio.Task[None] | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def bound_port(self) -> int:
        """Return the selected TCP port after start (useful when port=0)."""

        if self._server is None or not self._server.sockets:
            raise RuntimeError("gateway has not been started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        """Bind the listening socket without blocking forever."""

        if self._server is not None:
            raise RuntimeError("gateway is already running")
        self._server = await asyncio.start_server(
            self._accept_client,
            self.host,
            self.port,
            limit=self.max_message_bytes + 1,
        )
        self._liveness_task = asyncio.create_task(
            self._monitor_liveness(), name="gateway-liveness"
        )
        LOGGER.info("gateway listening on %s:%d", self.host, self.bound_port)

    async def serve_forever(self) -> None:
        """Run until cancelled; ``start`` must be called first."""

        if self._server is None:
            raise RuntimeError("gateway has not been started")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop accepting clients, close current clients, and await handlers."""

        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()

        if self._liveness_task is not None:
            self._liveness_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._liveness_task
            self._liveness_task = None

        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers), return_exceptions=True
            )

        current = asyncio.current_task()
        tasks = tuple(task for task in self._client_tasks if task is not current)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        LOGGER.info("gateway stopped")

    async def _accept_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self._writers.add(writer)
        raw_socket: socket.socket | None = writer.get_extra_info("socket")
        if raw_socket is not None:
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.stats.connections_accepted += 1
        self.stats.active_connections += 1
        peer = writer.get_extra_info("peername")
        connection_nodes: set[str] = set()
        self._emit_gateway_event(
            "connection_accepted", details={"peer": str(peer)}
        )
        LOGGER.debug("client connected: %s", peer)

        try:
            await self._read_client(reader, connection_nodes)
        except (ConnectionError, TimeoutError):
            LOGGER.debug("client connection ended: %s", peer)
        except Exception:
            # A callback or unexpected client failure must not take down the listener.
            LOGGER.exception("client handler failed: %s", peer)
        finally:
            self.stats.active_connections -= 1
            for node_id in connection_nodes:
                self.registry.connection_closed(node_id)
                self._emit_gateway_event("connection_closed", node_id=node_id)
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if task is not None:
                self._client_tasks.discard(task)
            LOGGER.debug("client disconnected: %s", peer)

    async def _read_client(
        self, reader: asyncio.StreamReader, connection_nodes: set[str]
    ) -> None:
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=self.client_idle_timeout
                )
            except ValueError:
                # StreamReader raises ValueError if a line exceeds its configured limit.
                self.stats.overlong_messages += 1
                self._emit_gateway_event("oversized_message")
                LOGGER.warning("closing client after overlong message")
                return
            except TimeoutError:
                self.stats.idle_disconnects += 1
                self._emit_gateway_event("idle_disconnect")
                raise

            if not line:
                return
            if not line.endswith(b"\n"):
                self.stats.truncated_messages += 1
                self._emit_gateway_event("truncated_eof")
                LOGGER.warning("discarding incomplete message at EOF")
                return
            if len(line) - 1 > self.max_message_bytes:
                self.stats.overlong_messages += 1
                self._emit_gateway_event("oversized_message")
                LOGGER.warning("closing client after overlong message")
                return

            wire_bytes = len(line)
            received_at_ms = time.time_ns() // 1_000_000
            received_monotonic_ns = time.monotonic_ns()
            try:
                message = parse_message(line[:-1])
            except ProtocolError as exc:
                if exc.reason == "malformed_json":
                    self.stats.malformed_json += 1
                    self._emit_gateway_event("malformed_json")
                else:
                    self.stats.schema_rejections += 1
                    if self._is_reading_candidate(line):
                        self.stats.rejected_readings += 1
                    self._emit_gateway_event(self._schema_event_type(line))
                LOGGER.warning("discarding malformed or invalid sensor message")
                continue

            self.stats.valid_messages += 1
            self.stats.sensor_bytes += wire_bytes
            existing = self.registry.nodes.get(message.node_id)
            transition_count = 0 if existing is None else len(existing.transitions)
            sequence_status = self.registry.observe(
                message,
                received_at_ms=received_at_ms,
                received_monotonic_ns=received_monotonic_ns,
            )
            for transition in self.registry.nodes[message.node_id].transitions[
                transition_count:
            ]:
                self._handle_transition(transition)
            if sequence_status not in (SequenceStatus.FIRST, SequenceStatus.IN_ORDER):
                self._emit_gateway_event(
                    sequence_status.value.lower(),
                    node_id=message.node_id,
                    timestamp_ms=received_at_ms,
                    details={
                        "sequence": message.sequence,
                        "last_sequence": self.registry.nodes[message.node_id].last_sequence,
                    },
                )
            if message.node_id not in connection_nodes:
                connection_nodes.add(message.node_id)
                self.registry.connection_opened(message.node_id)
                self._emit_gateway_event(
                    "connection_opened",
                    node_id=message.node_id,
                    timestamp_ms=received_at_ms,
                )
            if self.on_message is not None:
                received = ReceivedMessage(
                    message=message,
                    received_at_ms=received_at_ms,
                    received_monotonic_ns=received_monotonic_ns,
                    wire_bytes=wire_bytes,
                    sequence_status=sequence_status,
                )
                if inspect.iscoroutinefunction(self.on_message):
                    result = self.on_message(received)
                else:
                    result = await asyncio.to_thread(self.on_message, received)
                if inspect.isawaitable(result):
                    await result

    async def _monitor_liveness(self) -> None:
        while True:
            await asyncio.sleep(self.liveness_check_interval)
            for transition in self.registry.refresh_health():
                self._handle_transition(transition)

    def _handle_transition(self, transition: HealthTransition) -> None:
        LOGGER.info(
            "health node=%s previous=%s current=%s timestamp_ms=%d",
            transition.node_id,
            transition.previous,
            transition.current,
            transition.timestamp_ms,
        )
        if self.on_health_event is None:
            return
        record = self.registry.nodes[transition.node_id]
        reason = (
            "initial_message"
            if transition.previous is None
            else "valid_message_recovery"
            if transition.current.value == "ONLINE"
            else "liveness_timeout"
        )
        self.on_health_event(
            HealthEvent(
                timestamp_ms=transition.timestamp_ms,
                node_id=record.node_id,
                node_kind=record.node_kind,
                old_state="" if transition.previous is None else transition.previous.value,
                new_state=transition.current.value,
                reason=reason,
                last_seen_ms=record.last_seen_ms,
                last_sequence=record.last_sequence,
            )
        )

    def _emit_gateway_event(
        self,
        event_type: str,
        *,
        node_id: str = "",
        timestamp_ms: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.on_gateway_event is None:
            return
        self.on_gateway_event(
            GatewayEvent(
                timestamp_ms=time.time_ns() // 1_000_000
                if timestamp_ms is None
                else timestamp_ms,
                source="gateway",
                event_type=event_type,
                node_id=node_id,
                details={} if details is None else details,
            )
        )

    @staticmethod
    def _is_reading_candidate(line: bytes) -> bool:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(value, dict) and value.get("type") == "reading"

    @staticmethod
    def _schema_event_type(line: bytes) -> str:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "invalid_message"
        if isinstance(value, dict) and value.get("version") != 1:
            return "unsupported_protocol_version"
        return "invalid_message"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the hybrid WSN edge gateway")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--raw-output",
        type=Path,
        help="persist validated messages as new NDJSON evidence (refuses overwrite)",
    )
    parser.add_argument(
        "--upstream-mode",
        choices=("disabled", "raw", "aggregated"),
        default="disabled",
    )
    parser.add_argument("--collector-host", default="127.0.0.1")
    parser.add_argument("--collector-port", type=int, default=9662)
    parser.add_argument("--aggregation-window", type=float, default=5.0)
    parser.add_argument("--forwarder-queue-size", type=int, default=10_000)
    parser.add_argument("--storage-queue-size", type=int, default=10_000)
    parser.add_argument("--expected-interval", type=float, default=1.0)
    parser.add_argument("--suspect-after", type=float, default=3.0)
    parser.add_argument("--offline-after", type=float, default=5.0)
    parser.add_argument("--liveness-check-interval", type=float, default=0.5)
    parser.add_argument("--gateway-events-output", type=Path)
    parser.add_argument("--health-events-output", type=Path)
    parser.add_argument("--event-queue-size", type=int, default=10_000)
    parser.add_argument("--system-metrics-output", type=Path)
    parser.add_argument("--metrics-interval", type=float, default=0.5)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return parser.parse_args()


async def _run_from_cli(args: argparse.Namespace) -> None:
    # Local import avoids a module cycle: storage records ReceivedMessage metadata.
    from gateway.events import ExperimentEventRecorder
    from gateway.metrics import SystemMetricsSampler
    from gateway.storage import RawMessageStore
    from gateway.upstream import ForwardMode, UpstreamForwarder

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if (args.gateway_events_output is None) != (args.health_events_output is None):
        raise ValueError("gateway and health event outputs must be configured together")
    recorder = (
        ExperimentEventRecorder(
            args.gateway_events_output,
            args.health_events_output,
            queue_size=args.event_queue_size,
        )
        if args.gateway_events_output is not None
        else None
    )
    if recorder is not None:
        await recorder.start()
    metrics = (
        SystemMetricsSampler(
            args.system_metrics_output, interval_seconds=args.metrics_interval
        )
        if args.system_metrics_output is not None
        else None
    )
    if metrics is not None:
        await metrics.start()
    store = (
        RawMessageStore(args.raw_output, queue_size=args.storage_queue_size)
        if args.raw_output is not None
        else None
    )
    if store is not None:
        await store.start()
    forwarder = (
        UpstreamForwarder(
            args.collector_host,
            args.collector_port,
            mode=ForwardMode(args.upstream_mode),
            aggregation_window_seconds=args.aggregation_window,
            queue_size=args.forwarder_queue_size,
            on_event=None if recorder is None else recorder.record_gateway,
        )
        if args.upstream_mode != "disabled"
        else None
    )
    if forwarder is not None:
        await forwarder.start()

    async def log_message(received: ReceivedMessage) -> None:
        message = received.message
        LOGGER.info(
            "received node=%s type=%s sequence=%d status=%s "
            "gateway_timestamp_ms=%d bytes=%d",
            message.node_id,
            message.type,
            message.sequence,
            received.sequence_status,
            received.received_at_ms,
            received.wire_bytes,
        )
        if store is not None:
            await store.submit(received)
        if forwarder is not None:
            forwarder.try_submit(received)

    registry = NodeRegistry(
        expected_interval_seconds=args.expected_interval,
        suspect_after_intervals=args.suspect_after,
        offline_after_intervals=args.offline_after,
    )
    gateway = GatewayServer(
        args.host,
        args.port,
        registry=registry,
        liveness_check_interval=args.liveness_check_interval,
        on_message=log_message,
        on_gateway_event=None if recorder is None else recorder.record_gateway,
        on_health_event=None if recorder is None else recorder.record_health,
    )
    await gateway.start()
    try:
        await stop_event.wait()
    finally:
        await gateway.stop()
        if forwarder is not None:
            await forwarder.stop()
        if store is not None:
            await store.stop()
        if metrics is not None:
            await metrics.stop()
        if recorder is not None:
            await recorder.stop()
        if args.summary_output is not None:
            summary = {
                "gateway": {
                    **asdict(gateway.stats),
                    "invalid_messages": gateway.stats.invalid_messages,
                },
                "registry": {
                    node_id: asdict(record)
                    for node_id, record in gateway.registry.nodes.items()
                },
                "storage": None if store is None else asdict(store.stats),
                "upstream": None if forwarder is None else asdict(forwarder.stats),
                "events": None
                if recorder is None
                else {
                    "gateway": asdict(recorder.gateway_writer.stats),
                    "health": asdict(recorder.health_writer.stats),
                },
                "metrics": None if metrics is None else asdict(metrics.stats),
            }
            await asyncio.to_thread(_write_json_exclusive, args.summary_output, summary)


def _write_json_exclusive(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, default=str, sort_keys=True)
        output.write("\n")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run_from_cli(args))


if __name__ == "__main__":
    main()
