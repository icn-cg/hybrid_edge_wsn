import csv
import json
from pathlib import Path

from gateway.server import GatewayServer
from virtual_nodes.simulator import VirtualNodeSimulator


async def test_simulator_persists_node_accounting(tmp_path: Path) -> None:
    gateway = GatewayServer(port=0)
    await gateway.start()
    simulator = VirtualNodeSimulator(
        node_count=2,
        host="127.0.0.1",
        port=gateway.bound_port,
        sampling_interval=0.01,
        warmup_seconds=0.01,
        duration_seconds=0.04,
        seed=662,
        drop_probability=0.0,
        artificial_delay_seconds=0.0,
    )
    try:
        await simulator.run(tmp_path / "node_stats.csv", tmp_path / "simulator.json")
    finally:
        await gateway.stop()

    with (tmp_path / "node_stats.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    summary = json.loads((tmp_path / "simulator.json").read_text())
    assert len(rows) == 2
    assert all(int(row["scheduled_readings"]) == 4 for row in rows)
    assert all(int(row["successful_writes"]) >= 3 for row in rows)
    assert summary["measurement_start_ms"] <= summary["measurement_end_ms"]


async def test_simulator_failure_and_recovery_creates_new_incarnation(tmp_path: Path) -> None:
    gateway = GatewayServer(port=0, liveness_check_interval=0.005)
    await gateway.start()
    simulator = VirtualNodeSimulator(
        node_count=1,
        host="127.0.0.1",
        port=gateway.bound_port,
        sampling_interval=0.005,
        warmup_seconds=0.0,
        duration_seconds=0.06,
        seed=662,
        drop_probability=0.0,
        artificial_delay_seconds=0.0,
        failure_at_seconds=0.015,
        recovery_at_seconds=0.035,
    )
    try:
        await simulator.run(tmp_path / "nodes.csv", tmp_path / "simulator.json")
    finally:
        await gateway.stop()

    with (tmp_path / "nodes.csv").open(newline="") as source:
        row = next(csv.DictReader(source))
    summary = json.loads((tmp_path / "simulator.json").read_text())
    assert int(row["incarnations"]) == 2
    assert summary["failure_node_id"] == "virtual-000"
    assert summary["failure_timestamp_ms"] is not None
    assert summary["recovery_timestamp_ms"] is not None
