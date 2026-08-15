"""Concurrent NDJSON/TCP gateway for physical and virtual sensor nodes."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from gateway.protocol import ProtocolError, SensorMessage, parse_message

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


MessageHandler = Callable[[ReceivedMessage], Awaitable[None] | None]


@dataclass(slots=True)
class GatewayStats:
    """Phase 1 counters useful for smoke tests and operator logs."""

    connections_accepted: int = 0
    active_connections: int = 0
    valid_messages: int = 0
    invalid_messages: int = 0
    sensor_bytes: int = 0


class GatewayServer:
    """An asyncio server that frames and validates newline-delimited JSON."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        client_idle_timeout: float = 30.0,
        on_message: MessageHandler | None = None,
    ) -> None:
        if max_message_bytes < 1:
            raise ValueError("max_message_bytes must be positive")
        if client_idle_timeout <= 0:
            raise ValueError("client_idle_timeout must be positive")

        self.host = host
        self.port = port
        self.max_message_bytes = max_message_bytes
        self.client_idle_timeout = client_idle_timeout
        self.on_message = on_message
        self.stats = GatewayStats()
        self._server: asyncio.Server | None = None
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
        self.stats.connections_accepted += 1
        self.stats.active_connections += 1
        peer = writer.get_extra_info("peername")
        LOGGER.debug("client connected: %s", peer)

        try:
            await self._read_client(reader)
        except (ConnectionError, TimeoutError):
            LOGGER.debug("client connection ended: %s", peer)
        except Exception:
            # A callback or unexpected client failure must not take down the listener.
            LOGGER.exception("client handler failed: %s", peer)
        finally:
            self.stats.active_connections -= 1
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if task is not None:
                self._client_tasks.discard(task)
            LOGGER.debug("client disconnected: %s", peer)

    async def _read_client(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=self.client_idle_timeout
                )
            except ValueError:
                # StreamReader raises ValueError if a line exceeds its configured limit.
                self.stats.invalid_messages += 1
                LOGGER.warning("closing client after overlong message")
                return

            if not line:
                return
            if not line.endswith(b"\n"):
                self.stats.invalid_messages += 1
                LOGGER.warning("discarding incomplete message at EOF")
                return
            if len(line) - 1 > self.max_message_bytes:
                self.stats.invalid_messages += 1
                LOGGER.warning("closing client after overlong message")
                return

            wire_bytes = len(line)
            try:
                message = parse_message(line[:-1])
            except ProtocolError:
                self.stats.invalid_messages += 1
                LOGGER.warning("discarding malformed or invalid sensor message")
                continue

            self.stats.valid_messages += 1
            self.stats.sensor_bytes += wire_bytes
            if self.on_message is not None:
                received = ReceivedMessage(
                    message=message,
                    received_at_ms=time.time_ns() // 1_000_000,
                    received_monotonic_ns=time.monotonic_ns(),
                    wire_bytes=wire_bytes,
                )
                result = self.on_message(received)
                if inspect.isawaitable(result):
                    await result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the hybrid WSN edge gateway")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return parser.parse_args()


async def _run_from_cli(args: argparse.Namespace) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    def log_message(received: ReceivedMessage) -> None:
        message = received.message
        LOGGER.info(
            "received node=%s type=%s sequence=%d gateway_timestamp_ms=%d bytes=%d",
            message.node_id,
            message.type,
            message.sequence,
            received.received_at_ms,
            received.wire_bytes,
        )

    gateway = GatewayServer(args.host, args.port, on_message=log_message)
    await gateway.start()
    try:
        await stop_event.wait()
    finally:
        await gateway.stop()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run_from_cli(args))


if __name__ == "__main__":
    main()
