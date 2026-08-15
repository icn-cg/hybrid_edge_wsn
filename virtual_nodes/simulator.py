"""One-process orchestration for many independently connected virtual nodes."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from virtual_nodes.node import VirtualNode, VirtualNodeConfig


@dataclass(slots=True)
class NodeTotals:
    samples_generated: int = 0
    send_attempts: int = 0
    successful_writes: int = 0
    application_drops: int = 0

    def add(self, node: VirtualNode) -> None:
        self.samples_generated += node.samples_generated
        self.send_attempts += node.send_attempts
        self.successful_writes += node.messages_sent
        self.application_drops += node.application_drops

    def subtract(self, earlier: NodeTotals) -> NodeTotals:
        return NodeTotals(
            samples_generated=self.samples_generated - earlier.samples_generated,
            send_attempts=self.send_attempts - earlier.send_attempts,
            successful_writes=self.successful_writes - earlier.successful_writes,
            application_drops=self.application_drops - earlier.application_drops,
        )


class VirtualNodeSimulator:
    def __init__(
        self,
        *,
        node_count: int,
        host: str,
        port: int,
        sampling_interval: float,
        warmup_seconds: float,
        duration_seconds: float,
        seed: int,
        drop_probability: float,
        artificial_delay_seconds: float,
        failure_at_seconds: float | None = None,
        recovery_at_seconds: float | None = None,
    ) -> None:
        if node_count < 1:
            raise ValueError("node_count must be positive")
        if warmup_seconds < 0 or duration_seconds <= 0:
            raise ValueError("invalid warmup or duration")
        if failure_at_seconds is not None and not 0 <= failure_at_seconds < duration_seconds:
            raise ValueError("failure time must be inside measurement interval")
        if recovery_at_seconds is not None and (
            failure_at_seconds is None
            or recovery_at_seconds <= failure_at_seconds
            or recovery_at_seconds >= duration_seconds
        ):
            raise ValueError("recovery time must follow failure inside measurement interval")
        self.node_count = node_count
        self.host = host
        self.port = port
        self.sampling_interval = sampling_interval
        self.warmup_seconds = warmup_seconds
        self.duration_seconds = duration_seconds
        self.seed = seed
        self.drop_probability = drop_probability
        self.artificial_delay_seconds = artificial_delay_seconds
        self.failure_at_seconds = failure_at_seconds
        self.recovery_at_seconds = recovery_at_seconds
        self.incarnations: dict[str, list[VirtualNode]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.failure_timestamp_ms: int | None = None
        self.recovery_timestamp_ms: int | None = None

    async def run(self, stats_path: Path, summary_path: Path) -> None:
        run_start_ms = time.time_ns() // 1_000_000
        for index in range(self.node_count):
            self._start_incarnation(index)
        try:
            await asyncio.sleep(self.warmup_seconds)
            measurement_start_ms = time.time_ns() // 1_000_000
            baseline = self._totals()
            failure_task = (
                asyncio.create_task(self._failure_sequence(), name="failure-sequence")
                if self.failure_at_seconds is not None
                else None
            )
            await asyncio.sleep(self.duration_seconds)
            measurement_end_ms = time.time_ns() // 1_000_000
            if failure_task is not None:
                failure_task.cancel()
                await asyncio.gather(failure_task, return_exceptions=True)
        finally:
            await self._stop_all()
        run_end_ms = time.time_ns() // 1_000_000
        final = self._totals()
        self._write_stats(stats_path, baseline, final)
        self._write_json(
            summary_path,
            {
                "run_start_ms": run_start_ms,
                "measurement_start_ms": measurement_start_ms,
                "measurement_end_ms": measurement_end_ms,
                "run_end_ms": run_end_ms,
                "failure_node_id": (
                    "virtual-000" if self.failure_at_seconds is not None else None
                ),
                "failure_timestamp_ms": self.failure_timestamp_ms,
                "recovery_timestamp_ms": self.recovery_timestamp_ms,
            },
        )

    def _start_incarnation(self, index: int) -> None:
        node_id = f"virtual-{index:03d}"
        node = VirtualNode(
            VirtualNodeConfig(
                node_id=node_id,
                host=self.host,
                port=self.port,
                sampling_interval=self.sampling_interval,
                drop_probability=self.drop_probability,
                artificial_delay=self.artificial_delay_seconds,
                seed=self.seed + index,
                reconnect_initial=min(0.1, self.sampling_interval),
            )
        )
        self.incarnations.setdefault(node_id, []).append(node)
        self.tasks[node_id] = asyncio.create_task(node.run(), name=node_id)

    async def _failure_sequence(self) -> None:
        assert self.failure_at_seconds is not None
        await asyncio.sleep(self.failure_at_seconds)
        self.failure_timestamp_ms = time.time_ns() // 1_000_000
        await self._stop_node("virtual-000")
        if self.recovery_at_seconds is not None:
            await asyncio.sleep(self.recovery_at_seconds - self.failure_at_seconds)
            self._start_incarnation(0)
            self.recovery_timestamp_ms = time.time_ns() // 1_000_000

    async def _stop_node(self, node_id: str) -> None:
        task = self.tasks.pop(node_id, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _stop_all(self) -> None:
        tasks = tuple(self.tasks.values())
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _totals(self) -> dict[str, NodeTotals]:
        totals: dict[str, NodeTotals] = {}
        for node_id, nodes in self.incarnations.items():
            total = NodeTotals()
            for node in nodes:
                total.add(node)
            totals[node_id] = total
        return totals

    def _write_stats(
        self,
        path: Path,
        baseline: dict[str, NodeTotals],
        final: dict[str, NodeTotals],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        scheduled = math.floor(self.duration_seconds / self.sampling_interval)
        fields = (
            "node_id",
            "node_kind",
            "scheduled_readings",
            "samples_generated",
            "send_attempts",
            "successful_writes",
            "application_drops",
            "incarnations",
        )
        with path.open("x", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for node_id in sorted(final):
                measured = final[node_id].subtract(baseline[node_id])
                writer.writerow(
                    {
                        "node_id": node_id,
                        "node_kind": "virtual",
                        "scheduled_readings": scheduled,
                        **asdict(measured),
                        "incarnations": len(self.incarnations[node_id]),
                    }
                )

    @staticmethod
    def _write_json(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multiple virtual sensor nodes")
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8662)
    parser.add_argument("--sampling-interval", type=float, default=1.0)
    parser.add_argument("--warmup", type=float, default=0.0)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--seed", type=int, default=662)
    parser.add_argument("--drop-probability", type=float, default=0.0)
    parser.add_argument("--artificial-delay-ms", type=float, default=0.0)
    parser.add_argument("--failure-at", type=float)
    parser.add_argument("--recovery-at", type=float)
    parser.add_argument("--stats-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    simulator = VirtualNodeSimulator(
        node_count=args.nodes,
        host=args.host,
        port=args.port,
        sampling_interval=args.sampling_interval,
        warmup_seconds=args.warmup,
        duration_seconds=args.duration,
        seed=args.seed,
        drop_probability=args.drop_probability,
        artificial_delay_seconds=args.artificial_delay_ms / 1_000,
        failure_at_seconds=args.failure_at,
        recovery_at_seconds=args.recovery_at,
    )
    asyncio.run(simulator.run(args.stats_output, args.summary_output))


if __name__ == "__main__":
    main()
