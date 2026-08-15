"""Bounded asynchronous persistence for validated sensor messages."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.server import ReceivedMessage

_STOP = object()


@dataclass(slots=True)
class StorageStats:
    records_enqueued: int = 0
    records_written: int = 0
    bytes_written: int = 0


class RawMessageStore:
    """Write lossless NDJSON records via a bounded queue and worker thread calls."""

    def __init__(
        self,
        path: str | Path,
        *,
        queue_size: int = 10_000,
        batch_size: int = 100,
    ) -> None:
        if queue_size < 1 or batch_size < 1:
            raise ValueError("queue_size and batch_size must be positive")
        self.path = Path(path)
        self.batch_size = batch_size
        self.stats = StorageStats()
        self._queue: asyncio.Queue[ReceivedMessage | object] = asyncio.Queue(
            maxsize=queue_size
        )
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Create a new output file; existing raw evidence is never overwritten."""

        if self._worker is not None:
            raise RuntimeError("raw message store is already running")
        await asyncio.to_thread(self._create_output)
        self._worker = asyncio.create_task(self._write_loop(), name="raw-message-store")

    async def submit(self, received: ReceivedMessage) -> None:
        """Queue a validated message, applying backpressure instead of dropping evidence."""

        if self._worker is None:
            raise RuntimeError("raw message store is not running")
        await self._queue.put(received)
        self.stats.records_enqueued += 1

    async def stop(self) -> None:
        """Drain queued records and close the worker."""

        worker, self._worker = self._worker, None
        if worker is None:
            return
        await self._queue.put(_STOP)
        await worker

    async def _write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return

            batch = [item]
            should_stop = False
            while len(batch) < self.batch_size:
                try:
                    queued = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is _STOP:
                    self._queue.task_done()
                    should_stop = True
                    break
                batch.append(queued)

            lines = [self._encode_record(received) for received in batch]
            bytes_written = await asyncio.to_thread(self._append_lines, lines)
            self.stats.records_written += len(lines)
            self.stats.bytes_written += bytes_written
            for _ in batch:
                self._queue.task_done()
            if should_stop:
                return

    def _create_output(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("x", encoding="utf-8", newline="\n"):
            pass

    def _append_lines(self, lines: list[str]) -> int:
        text = "".join(lines)
        with self.path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
        return len(text.encode("utf-8"))

    @staticmethod
    def _encode_record(received: ReceivedMessage) -> str:
        record: dict[str, Any] = received.message.model_dump()
        record.update(
            {
                "gateway_received_at_ms": received.received_at_ms,
                "gateway_received_monotonic_ns": received.received_monotonic_ns,
                "sensor_wire_bytes": received.wire_bytes,
                "sequence_status": received.sequence_status.value,
            }
        )
        return json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
