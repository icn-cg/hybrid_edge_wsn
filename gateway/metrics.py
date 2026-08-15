"""Low-frequency gateway process and host resource sampling."""

from __future__ import annotations

import asyncio
import csv
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(slots=True)
class MetricsStats:
    samples_written: int = 0


class SystemMetricsSampler:
    def __init__(
        self,
        path: str | Path,
        *,
        interval_seconds: float = 0.5,
        process: psutil.Process | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self.process = process or psutil.Process()
        self.stats = MetricsStats()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("metrics sampler is already running")
        await asyncio.to_thread(self._create_output)
        self.process.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None)
        self._task = asyncio.create_task(self._run(), name="system-metrics")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            timestamp_ms = time.time_ns() // 1_000_000
            memory = self.process.memory_info()
            row = {
                "timestamp_ms": timestamp_ms,
                "process_cpu_percent": self.process.cpu_percent(interval=None),
                "process_rss_bytes": memory.rss,
                "process_vms_bytes": memory.vms,
                "system_cpu_percent": psutil.cpu_percent(interval=None),
            }
            await asyncio.to_thread(self._append_row, row)
            self.stats.samples_written += 1

    def _create_output(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("x", encoding="utf-8", newline="") as output:
            csv.DictWriter(
                output,
                fieldnames=(
                    "timestamp_ms",
                    "process_cpu_percent",
                    "process_rss_bytes",
                    "process_vms_bytes",
                    "system_cpu_percent",
                ),
            ).writeheader()

    def _append_row(self, row: dict[str, int | float]) -> None:
        with self.path.open("a", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=tuple(row))
            writer.writerow(row)
            output.flush()
