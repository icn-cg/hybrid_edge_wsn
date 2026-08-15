"""Structured, bounded event persistence for experiment evidence."""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

_STOP = object()


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    timestamp_ms: int
    source: str
    event_type: str
    node_id: str = ""
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthEvent:
    timestamp_ms: int
    node_id: str
    node_kind: str
    old_state: str
    new_state: str
    reason: str
    last_seen_ms: int
    last_sequence: int


@dataclass(slots=True)
class EventWriterStats:
    enqueued: int = 0
    written: int = 0
    queue_full_drops: int = 0


class AsyncCsvWriter[T]:
    """A fixed-schema CSV sink with non-blocking producer and threaded batches."""

    def __init__(
        self,
        path: str | Path,
        fieldnames: tuple[str, ...],
        row_factory: Callable[[T], dict[str, object]],
        *,
        queue_size: int = 10_000,
        batch_size: int = 100,
    ) -> None:
        if queue_size < 1 or batch_size < 1:
            raise ValueError("queue_size and batch_size must be positive")
        self.path = Path(path)
        self.fieldnames = fieldnames
        self.row_factory = row_factory
        self.batch_size = batch_size
        self.stats = EventWriterStats()
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("event writer is already running")
        await asyncio.to_thread(self._create_output)
        self._worker = asyncio.create_task(self._run(), name=f"csv:{self.path.name}")

    def try_submit(self, item: T) -> bool:
        if self._worker is None:
            raise RuntimeError("event writer is not running")
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.queue_full_drops += 1
            return False
        self.stats.enqueued += 1
        return True

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        await self._queue.put(_STOP)
        await worker

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            if first is _STOP:
                self._queue.task_done()
                return
            batch = [cast(T, first)]
            should_stop = False
            while len(batch) < self.batch_size:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _STOP:
                    self._queue.task_done()
                    should_stop = True
                    break
                batch.append(cast(T, item))
            rows = [self.row_factory(item) for item in batch]
            await asyncio.to_thread(self._append_rows, rows)
            self.stats.written += len(rows)
            for _ in batch:
                self._queue.task_done()
            if should_stop:
                return

    def _create_output(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("x", encoding="utf-8", newline="") as output:
            csv.DictWriter(output, fieldnames=self.fieldnames).writeheader()

    def _append_rows(self, rows: list[dict[str, object]]) -> None:
        with self.path.open("a", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=self.fieldnames)
            writer.writerows(rows)
            output.flush()


class ExperimentEventRecorder:
    def __init__(
        self,
        gateway_events_path: str | Path,
        health_events_path: str | Path,
        *,
        queue_size: int = 10_000,
    ) -> None:
        self.gateway_writer = AsyncCsvWriter[GatewayEvent](
            gateway_events_path,
            ("timestamp_ms", "source", "event_type", "node_id", "details_json"),
            self._gateway_row,
            queue_size=queue_size,
        )
        self.health_writer = AsyncCsvWriter[HealthEvent](
            health_events_path,
            (
                "timestamp_ms",
                "node_id",
                "node_kind",
                "old_state",
                "new_state",
                "reason",
                "last_seen_ms",
                "last_sequence",
            ),
            self._health_row,
            queue_size=queue_size,
        )

    async def start(self) -> None:
        await self.gateway_writer.start()
        try:
            await self.health_writer.start()
        except Exception:
            await self.gateway_writer.stop()
            raise

    async def stop(self) -> None:
        await self.gateway_writer.stop()
        await self.health_writer.stop()

    def record_gateway(self, event: GatewayEvent) -> bool:
        return self.gateway_writer.try_submit(event)

    def record_health(self, event: HealthEvent) -> bool:
        return self.health_writer.try_submit(event)

    @staticmethod
    def _gateway_row(event: GatewayEvent) -> dict[str, object]:
        return {
            "timestamp_ms": event.timestamp_ms,
            "source": event.source,
            "event_type": event.event_type,
            "node_id": event.node_id,
            "details_json": json.dumps(event.details, separators=(",", ":"), sort_keys=True),
        }

    @staticmethod
    def _health_row(event: HealthEvent) -> dict[str, object]:
        return {
            "timestamp_ms": event.timestamp_ms,
            "node_id": event.node_id,
            "node_kind": event.node_kind,
            "old_state": event.old_state,
            "new_state": event.new_state,
            "reason": event.reason,
            "last_seen_ms": event.last_seen_ms,
            "last_sequence": event.last_sequence,
        }
