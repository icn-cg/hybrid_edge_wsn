"""Reliable gateway-side RAW or AGGREGATED forwarding to the upstream collector."""

from __future__ import annotations

import asyncio
import socket
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from gateway.aggregator import WindowAggregator, WindowSummary
from gateway.protocol import ReadingMessage
from gateway.upstream_protocol import (
    AggregateUpstreamRecord,
    RawUpstreamRecord,
    UpstreamRecord,
    aggregate_record,
    encode_upstream_record,
)

if TYPE_CHECKING:
    from gateway.server import ReceivedMessage

_STOP = object()


class ForwardMode(StrEnum):
    RAW = "raw"
    AGGREGATED = "aggregated"


@dataclass(slots=True)
class UpstreamStats:
    readings_enqueued: int = 0
    upstream_messages: int = 0
    upstream_bytes: int = 0
    connection_attempts: int = 0
    connection_failures: int = 0
    send_failures: int = 0
    records_abandoned_on_shutdown: int = 0
    queue_full_drops: int = 0


class UpstreamForwarder:
    """Queue readings and deliver collector records with reconnect/backpressure."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        mode: ForwardMode | str,
        aggregation_window_seconds: float = 5.0,
        queue_size: int = 10_000,
        reconnect_initial: float = 0.1,
        reconnect_max: float = 5.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        if reconnect_initial <= 0 or reconnect_max < reconnect_initial:
            raise ValueError("invalid reconnect backoff")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        self.host = host
        self.port = port
        self.mode = ForwardMode(mode)
        if self.mode is ForwardMode.AGGREGATED and aggregation_window_seconds <= 0:
            raise ValueError("aggregation_window_seconds must be positive")
        self.aggregation_window_seconds = aggregation_window_seconds
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.shutdown_timeout = shutdown_timeout
        self.stats = UpstreamStats()
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._inflight = False

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("upstream forwarder is already running")
        self._worker = asyncio.create_task(self._run(), name="upstream-forwarder")

    async def submit(self, received: ReceivedMessage) -> None:
        """Queue readings only; heartbeats remain sensor-to-edge liveness messages."""

        if self._worker is None:
            raise RuntimeError("upstream forwarder is not running")
        if not isinstance(received.message, ReadingMessage):
            return
        await self._queue.put(received)
        self.stats.readings_enqueued += 1

    def try_submit(self, received: ReceivedMessage) -> bool:
        """Enqueue a reading without blocking the sensor/persist path.

        Returns False and increments ``queue_full_drops`` when the collector
        path is saturated. Heartbeats are ignored and count as success.
        """

        if self._worker is None:
            raise RuntimeError("upstream forwarder is not running")
        if not isinstance(received.message, ReadingMessage):
            return True
        try:
            self._queue.put_nowait(received)
        except asyncio.QueueFull:
            self.stats.queue_full_drops += 1
            return False
        self.stats.readings_enqueued += 1
        return True

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            await asyncio.wait_for(self._queue.put(_STOP), timeout=self.shutdown_timeout)
            await asyncio.wait_for(worker, timeout=self.shutdown_timeout)
        except TimeoutError:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            while True:
                try:
                    queued = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is not _STOP:
                    self.stats.records_abandoned_on_shutdown += 1
                self._queue.task_done()
        finally:
            await self._close_connection()

    async def _run(self) -> None:
        if self.mode is ForwardMode.RAW:
            await self._run_raw()
        else:
            await self._run_aggregated()

    async def _run_raw(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                received = cast("ReceivedMessage", item)
                reading = received.message
                assert isinstance(reading, ReadingMessage)
                record = RawUpstreamRecord(
                    type="raw",
                    version=1,
                    record_id=(
                        f"raw:{reading.node_id}:{reading.sequence}:"
                        f"{received.received_monotonic_ns}"
                    ),
                    forwarded_at_ms=time.time_ns() // 1_000_000,
                    gateway_received_at_ms=received.received_at_ms,
                    gateway_received_monotonic_ns=received.received_monotonic_ns,
                    sensor_wire_bytes=received.wire_bytes,
                    sequence_status=received.sequence_status,
                    reading=reading,
                )
                await self._send_record(record)
            finally:
                self._queue.task_done()

    async def _run_aggregated(self) -> None:
        aggregator = WindowAggregator(self.aggregation_window_seconds)
        while True:
            timeout = self._aggregation_timeout(aggregator)
            try:
                if timeout is None:
                    item = await self._queue.get()
                else:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                summary = aggregator.flush(partial_window=False)
                if summary is not None:
                    await self._send_summary(summary)
                continue

            try:
                if item is _STOP:
                    summary = aggregator.flush(
                        partial_window=True,
                        window_end_ms=time.time_ns() // 1_000_000,
                    )
                    if summary is not None:
                        await self._send_summary(summary)
                    return
                received = cast("ReceivedMessage", item)
                completed = aggregator.add(received)
                if completed is not None:
                    await self._send_summary(completed)
            finally:
                self._queue.task_done()

    @staticmethod
    def _aggregation_timeout(aggregator: WindowAggregator) -> float | None:
        deadline = aggregator.deadline_monotonic_ns
        if deadline is None:
            return None
        return max(0.0, (deadline - time.monotonic_ns()) / 1_000_000_000)

    async def _send_summary(self, summary: WindowSummary) -> None:
        record = aggregate_record(summary, forwarded_at_ms=time.time_ns() // 1_000_000)
        await self._send_record(record)

    async def _send_record(
        self, record: RawUpstreamRecord | AggregateUpstreamRecord | UpstreamRecord
    ) -> None:
        encoded = encode_upstream_record(record)
        self._inflight = True
        try:
            backoff = self.reconnect_initial
            while True:
                try:
                    writer = await self._connection()
                    writer.write(encoded)
                    await writer.drain()
                except (ConnectionError, OSError):
                    self.stats.send_failures += 1
                    await self._close_connection()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.reconnect_max)
                    continue
                self.stats.upstream_messages += 1
                self.stats.upstream_bytes += len(encoded)
                return
        except asyncio.CancelledError:
            self.stats.records_abandoned_on_shutdown += 1
            raise
        finally:
            self._inflight = False

    async def _connection(self) -> asyncio.StreamWriter:
        if self._writer is not None and not self._writer.is_closing():
            return self._writer
        self.stats.connection_attempts += 1
        try:
            _reader, writer = await asyncio.open_connection(self.host, self.port)
        except (ConnectionError, OSError):
            self.stats.connection_failures += 1
            raise
        raw_socket: socket.socket | None = writer.get_extra_info("socket")
        if raw_socket is not None:
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._writer = writer
        return writer

    async def _close_connection(self) -> None:
        writer, self._writer = self._writer, None
        if writer is None:
            return
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()
