import asyncio
import csv
from pathlib import Path

from gateway.events import ExperimentEventRecorder, GatewayEvent, HealthEvent
from gateway.metrics import SystemMetricsSampler


async def test_event_recorder_persists_gateway_and_health_rows(tmp_path: Path) -> None:
    recorder = ExperimentEventRecorder(
        tmp_path / "gateway_events.csv", tmp_path / "health_events.csv", queue_size=2
    )
    await recorder.start()
    recorder.record_gateway(
        GatewayEvent(1_000, "upstream", "queue_full_drop", "virtual-001", {"size": 2})
    )
    recorder.record_health(
        HealthEvent(1_001, "virtual-001", "virtual", "ONLINE", "SUSPECT", "timeout", 900, 7)
    )
    await recorder.stop()

    with (tmp_path / "gateway_events.csv").open(newline="") as source:
        gateway_rows = list(csv.DictReader(source))
    with (tmp_path / "health_events.csv").open(newline="") as source:
        health_rows = list(csv.DictReader(source))
    assert gateway_rows[0]["event_type"] == "queue_full_drop"
    assert gateway_rows[0]["details_json"] == '{"size":2}'
    assert health_rows[0]["new_state"] == "SUSPECT"
    assert recorder.gateway_writer.stats.written == 1
    assert recorder.health_writer.stats.written == 1


async def test_metrics_sampler_starts_stops_and_produces_rows(tmp_path: Path) -> None:
    output = tmp_path / "system_metrics.csv"
    sampler = SystemMetricsSampler(output, interval_seconds=0.01)
    await sampler.start()
    await asyncio.sleep(0.035)
    await sampler.stop()

    with output.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) >= 2
    assert int(rows[0]["process_rss_bytes"]) > 0
    assert sampler.stats.samples_written == len(rows)
