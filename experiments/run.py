"""Subprocess experiment runner producing one immutable evidence directory per run."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from experiments.config import ExperimentConfig
from experiments.manifest import (
    RunArtifacts,
    build_manifest,
    new_run_id,
    write_json_exclusive,
)

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = REPOSITORY / "results" / "raw"


class ExperimentChildError(RuntimeError):
    pass


@dataclass(slots=True)
class Child:
    name: str
    process: asyncio.subprocess.Process
    log_handle: object


class ExperimentRunner:
    def __init__(
        self,
        *,
        results_root: str | Path = DEFAULT_RESULTS_ROOT,
        repository: str | Path = REPOSITORY,
        collector_module: str = "collector.server",
        gateway_module: str = "gateway.server",
        simulator_module: str = "virtual_nodes.simulator",
    ) -> None:
        self.results_root = Path(results_root)
        self.repository = Path(repository)
        self.collector_module = collector_module
        self.gateway_module = gateway_module
        self.simulator_module = simulator_module

    async def run_once(self, config: ExperimentConfig) -> RunArtifacts:
        gateway_port = config.gateway_port or _free_port()
        collector_port = config.collector_port or _free_port()
        while collector_port == gateway_port:
            collector_port = _free_port()
        config = config.model_copy(
            update={"gateway_port": gateway_port, "collector_port": collector_port}
        )
        artifacts = RunArtifacts.create(
            self.results_root, new_run_id(config.experiment_type)
        )
        write_json_exclusive(
            artifacts.manifest,
            build_manifest(config, artifacts.run_id, repository=self.repository),
        )
        children: list[Child] = []
        status = "failed"
        error: str | None = None
        runner_start_ms = time.time_ns() // 1_000_000
        try:
            collector = await self._start_child(
                "collector",
                self.collector_module,
                [
                    "--host",
                    config.collector_host,
                    "--port",
                    str(config.collector_port),
                    "--output",
                    str(artifacts.upstream),
                    "--summary-output",
                    str(artifacts.collector_summary),
                    "--log-level",
                    "WARNING",
                ],
                artifacts,
            )
            children.append(collector)
            await _wait_for_port(
                config.collector_host, config.collector_port, collector.process
            )

            gateway = await self._start_child(
                "gateway",
                self.gateway_module,
                self._gateway_args(config, artifacts),
                artifacts,
            )
            children.append(gateway)
            await _wait_for_port(config.gateway_host, config.gateway_port, gateway.process)

            simulator = await self._start_child(
                "simulator",
                self.simulator_module,
                self._simulator_args(config, artifacts),
                artifacts,
            )
            children.append(simulator)
            await asyncio.wait_for(
                simulator.process.wait(),
                timeout=config.warmup_seconds + config.duration_seconds + 20,
            )
            _ensure_success(simulator.name, simulator.process.returncode)
            await _stop_child(gateway)
            await _stop_child(collector)
            _ensure_success(gateway.name, gateway.process.returncode)
            _ensure_success(collector.name, collector.process.returncode)
            status = "complete"
        except asyncio.CancelledError:
            status = "interrupted"
            error = "runner task cancelled"
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            for child in reversed(children):
                await _stop_child(child)
                child.log_handle.close()
            summary = self._build_summary(
                artifacts,
                config,
                status=status,
                error=error,
                runner_start_ms=runner_start_ms,
                runner_end_ms=time.time_ns() // 1_000_000,
                children=children,
            )
            if not artifacts.run_summary.exists():
                write_json_exclusive(artifacts.run_summary, summary)
        return artifacts

    async def _start_child(
        self,
        name: str,
        module: str,
        args: list[str],
        artifacts: RunArtifacts,
    ) -> Child:
        log_handle = (artifacts.run_dir / f"{name}.log").open("xb")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                module,
                *args,
                cwd=self.repository,
                stdout=log_handle,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception:
            log_handle.close()
            raise
        return Child(name, process, log_handle)

    @staticmethod
    def _gateway_args(config: ExperimentConfig, artifacts: RunArtifacts) -> list[str]:
        return [
            "--host",
            config.gateway_host,
            "--port",
            str(config.gateway_port),
            "--raw-output",
            str(artifacts.readings),
            "--upstream-mode",
            config.aggregation_mode,
            "--collector-host",
            config.collector_host,
            "--collector-port",
            str(config.collector_port),
            "--aggregation-window",
            str(config.aggregation_window_seconds or 1),
            "--forwarder-queue-size",
            str(config.forwarder_queue_size),
            "--storage-queue-size",
            str(config.storage_queue_size),
            "--expected-interval",
            str(config.expected_interval_seconds),
            "--suspect-after",
            str(config.suspect_after_intervals),
            "--offline-after",
            str(config.offline_after_intervals),
            "--liveness-check-interval",
            str(config.liveness_check_interval_seconds),
            "--gateway-events-output",
            str(artifacts.gateway_events),
            "--health-events-output",
            str(artifacts.health_events),
            "--event-queue-size",
            str(config.event_queue_size),
            "--system-metrics-output",
            str(artifacts.system_metrics),
            "--metrics-interval",
            str(config.metrics_interval_seconds),
            "--summary-output",
            str(artifacts.gateway_summary),
            "--log-level",
            "WARNING",
        ]

    @staticmethod
    def _simulator_args(config: ExperimentConfig, artifacts: RunArtifacts) -> list[str]:
        args = [
            "--nodes",
            str(config.virtual_node_count),
            "--host",
            config.gateway_host,
            "--port",
            str(config.gateway_port),
            "--sampling-interval",
            str(config.sampling_interval_ms / 1_000),
            "--warmup",
            str(config.warmup_seconds),
            "--duration",
            str(config.duration_seconds),
            "--seed",
            str(config.random_seed),
            "--drop-probability",
            str(config.drop_probability),
            "--artificial-delay-ms",
            str(config.artificial_delay_ms),
            "--stats-output",
            str(artifacts.node_stats),
            "--summary-output",
            str(artifacts.simulator_summary),
        ]
        if config.failure_at_seconds is not None:
            args.extend(("--failure-at", str(config.failure_at_seconds)))
        if config.recovery_at_seconds is not None:
            args.extend(("--recovery-at", str(config.recovery_at_seconds)))
        return args

    @staticmethod
    def _build_summary(
        artifacts: RunArtifacts,
        config: ExperimentConfig,
        *,
        status: str,
        error: str | None,
        runner_start_ms: int,
        runner_end_ms: int,
        children: list[Child],
    ) -> dict[str, object]:
        return {
            "run_summary_schema_version": 1,
            "run_id": artifacts.run_id,
            "status": status,
            "error": error,
            "runner_start_ms": runner_start_ms,
            "runner_end_ms": runner_end_ms,
            "children": {child.name: child.process.returncode for child in children},
            "simulator": _read_json_if_present(artifacts.simulator_summary),
            "gateway": _read_json_if_present(artifacts.gateway_summary),
            "collector": _read_json_if_present(artifacts.collector_summary),
            "config": config.model_dump(),
        }


def _read_json_if_present(path: Path) -> object | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_port(
    host: str,
    port: int,
    process: asyncio.subprocess.Process,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            raise ExperimentChildError(f"child exited before listening: {process.returncode}")
        try:
            _reader, writer = await asyncio.open_connection(host, port)
        except (ConnectionError, OSError):
            await asyncio.sleep(0.02)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(f"timed out waiting for {host}:{port}")


async def _stop_child(child: Child, wait_seconds: float = 5.0) -> None:
    if child.process.returncode is not None:
        return
    child.process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(child.process.wait(), timeout=wait_seconds)
    except TimeoutError:
        child.process.terminate()
        try:
            await asyncio.wait_for(child.process.wait(), timeout=1.0)
        except TimeoutError:
            child.process.kill()
            await child.process.wait()


def _ensure_success(name: str, returncode: int | None) -> None:
    if returncode != 0:
        raise ExperimentChildError(f"{name} exited with status {returncode}")


def _parse_aggregation(value: str, window: float | None) -> tuple[str, float]:
    if value == "raw":
        return "raw", 0.0
    if value == "aggregated":
        if window is None or window <= 0:
            raise ValueError("--aggregation-window is required for aggregated mode")
        return "aggregated", window
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("aggregation must be raw, aggregated, or a window in seconds") from exc
    if parsed <= 0:
        raise ValueError("aggregation window must be positive")
    return "aggregated", parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible hybrid WSN experiments")
    parser.add_argument(
        "--experiment",
        choices=("scaling", "aggregation", "failure", "impairment"),
        required=True,
    )
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--warmup", type=float, default=0.0)
    parser.add_argument("--sampling-interval-ms", type=float, default=1_000.0)
    parser.add_argument("--aggregation", default="raw")
    parser.add_argument("--aggregation-window", type=float)
    parser.add_argument("--drop-probability", type=float, default=0.0)
    parser.add_argument("--artificial-delay-ms", type=float, default=0.0)
    parser.add_argument("--failure-at", type=float)
    parser.add_argument("--recovery-at", type=float)
    parser.add_argument("--seed", type=int, default=662)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--metrics-interval", type=float, default=0.5)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    return parser.parse_args()


async def _run_cli(args: argparse.Namespace) -> list[RunArtifacts]:
    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    mode, window = _parse_aggregation(args.aggregation, args.aggregation_window)
    impairment = (
        "application"
        if args.drop_probability > 0 or args.artificial_delay_ms > 0
        else "none"
    )
    failure_at = args.failure_at
    recovery_at = args.recovery_at
    if args.experiment == "failure" and failure_at is None:
        failure_at = args.duration / 3
        recovery_at = 2 * args.duration / 3
    runner = ExperimentRunner(results_root=args.results_root)
    completed: list[RunArtifacts] = []
    for repetition in range(args.repetitions):
        config = ExperimentConfig(
            experiment_type=args.experiment,
            node_count=args.nodes,
            virtual_node_count=args.nodes,
            duration_seconds=args.duration,
            warmup_seconds=args.warmup,
            sampling_interval_ms=args.sampling_interval_ms,
            aggregation_mode=mode,
            aggregation_window_seconds=window,
            impairment_mode=impairment,
            drop_probability=args.drop_probability,
            artificial_delay_ms=args.artificial_delay_ms,
            random_seed=args.seed + repetition,
            expected_interval_seconds=args.sampling_interval_ms / 1_000,
            liveness_check_interval_seconds=min(
                0.5, args.sampling_interval_ms / 2_000
            ),
            metrics_interval_seconds=args.metrics_interval,
            failure_at_seconds=failure_at,
            recovery_at_seconds=recovery_at,
        )
        artifacts = await runner.run_once(config)
        completed.append(artifacts)
        print(artifacts.run_dir)
    return completed


def main() -> None:
    args = _parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
