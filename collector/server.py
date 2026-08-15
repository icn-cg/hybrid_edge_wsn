"""Independent asyncio collector for gateway RAW and AGGREGATED records."""

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
from dataclasses import dataclass
from pathlib import Path

from gateway.upstream_protocol import (
    UpstreamProtocolError,
    UpstreamRecord,
    parse_upstream_record,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9662
DEFAULT_MAX_MESSAGE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class CollectedRecord:
    record: UpstreamRecord
    received_at_ms: int
    received_monotonic_ns: int
    wire_bytes: int


CollectorHandler = Callable[[CollectedRecord], Awaitable[None] | None]


@dataclass(slots=True)
class CollectorStats:
    connections_accepted: int = 0
    active_connections: int = 0
    messages_received: int = 0
    bytes_received: int = 0
    invalid_messages: int = 0
    overlong_messages: int = 0


class CollectorServer:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        output_path: str | Path | None = None,
        on_record: CollectorHandler | None = None,
    ) -> None:
        if max_message_bytes < 1:
            raise ValueError("max_message_bytes must be positive")
        self.host = host
        self.port = port
        self.max_message_bytes = max_message_bytes
        self.output_path = None if output_path is None else Path(output_path)
        self.on_record = on_record
        self.stats = CollectorStats()
        self._server: asyncio.Server | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("collector has not been started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("collector is already running")
        if self.output_path is not None:
            await asyncio.to_thread(self._create_output)
        self._server = await asyncio.start_server(
            self._accept_client,
            self.host,
            self.port,
            limit=self.max_message_bytes + 1,
        )
        LOGGER.info("collector listening on %s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
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
        LOGGER.info("collector stopped")

    async def _accept_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self._writers.add(writer)
        self.stats.connections_accepted += 1
        self.stats.active_connections += 1
        raw_socket: socket.socket | None = writer.get_extra_info("socket")
        if raw_socket is not None:
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    self.stats.overlong_messages += 1
                    return
                if not line:
                    return
                if not line.endswith(b"\n") or len(line) - 1 > self.max_message_bytes:
                    self.stats.overlong_messages += 1
                    return
                try:
                    record = parse_upstream_record(line[:-1])
                except UpstreamProtocolError:
                    self.stats.invalid_messages += 1
                    continue
                collected = CollectedRecord(
                    record=record,
                    received_at_ms=time.time_ns() // 1_000_000,
                    received_monotonic_ns=time.monotonic_ns(),
                    wire_bytes=len(line),
                )
                self.stats.messages_received += 1
                self.stats.bytes_received += len(line)
                if self.output_path is not None:
                    await asyncio.to_thread(self._append_output, collected)
                if self.on_record is not None:
                    if inspect.iscoroutinefunction(self.on_record):
                        result = self.on_record(collected)
                    else:
                        result = await asyncio.to_thread(self.on_record, collected)
                    if inspect.isawaitable(result):
                        await result
        except (ConnectionError, OSError):
            return
        finally:
            self.stats.active_connections -= 1
            self._writers.discard(writer)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            if task is not None:
                self._client_tasks.discard(task)

    def _create_output(self) -> None:
        assert self.output_path is not None
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("x", encoding="utf-8", newline="\n"):
            pass

    def _append_output(self, collected: CollectedRecord) -> None:
        assert self.output_path is not None
        value = {
            "collector_received_at_ms": collected.received_at_ms,
            "collector_received_monotonic_ns": collected.received_monotonic_ns,
            "upstream_wire_bytes": collected.wire_bytes,
            "record": collected.record.model_dump(mode="json"),
        }
        with self.output_path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
            output.flush()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the hybrid WSN upstream collector")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--output", type=Path, help="new NDJSON output file (refuses overwrite)")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return parser.parse_args()


async def _run_from_cli(args: argparse.Namespace) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    def log_record(collected: CollectedRecord) -> None:
        LOGGER.info(
            "collected type=%s collector_timestamp_ms=%d bytes=%d",
            collected.record.type,
            collected.received_at_ms,
            collected.wire_bytes,
        )

    collector = CollectorServer(
        args.host,
        args.port,
        output_path=args.output,
        on_record=log_record,
    )
    await collector.start()
    try:
        await stop_event.wait()
    finally:
        await collector.stop()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run_from_cli(args))


if __name__ == "__main__":
    main()
