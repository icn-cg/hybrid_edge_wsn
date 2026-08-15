# Hybrid Edge Wireless Sensor Network

A graduate Wireless Sensor Networks project for experimentally studying how edge aggregation
changes bandwidth use, latency, and reliability as a hybrid network scales and communication
degrades.

The current implementation is **Phase 3: reproducible virtual experiments**. It provides a
versioned sensor protocol, concurrent gateway and collector, immutable run manifests, persisted
health/gateway events, independently accounted virtual generation, CPU/memory sampling,
subprocess orchestration, and evidence-driven analysis. ESP32 firmware and physical hardware
integration remain future work.

## Research question

> How does edge aggregation affect bandwidth usage, latency, and reliability as a hybrid
> wireless sensor network scales and experiences degraded network conditions?

The testbed can measure gateway CPU/memory use, aggregation-window tradeoffs, failure/recovery
detection time, and controlled application-level impairment. No final scientific experiment matrix
or conclusions have been produced.

## Architecture

```text
Physical nodes (future)                         Mac upstream collector
BME280 -> ESP32 -- Wi-Fi/TCP --+                         ^
                               |                         | TCP/NDJSON
Virtual nodes -- TCP ----------+-> Raspberry Pi gateway-+
       (implemented)                 (runs on macOS now)
                                      validation
                                      registry/health
                                      raw persistence
                                      RAW or AGGREGATED forwarding
```

Physical and virtual nodes use the same newline-delimited JSON (NDJSON) sensor protocol.
`node_kind` identifies whether a reading is physical or synthetic; the gateway processing path is
otherwise identical. The collector uses a separate validated upstream protocol so aggregation
reduces traffic across a real second TCP connection rather than only reducing rows in a file.

## Implemented behavior

- `asyncio` TCP services with concurrent clients and no thread per sensor.
- Correct stream framing: split and coalesced TCP reads do not define message boundaries.
- Strict version, identity, sequence, timestamp, type, and environmental-value validation.
- Safety boundaries: 64 KiB messages, idle timeout, malformed-client isolation, bounded queues,
  exclusive evidence-file creation, and graceful shutdown.
- `TCP_NODELAY` on sensor-to-gateway and gateway-to-collector latency paths.
- Gateway wall-clock and monotonic receive timestamps plus exact NDJSON application-byte counts.
- Per-node sequence classification: FIRST, IN_ORDER, GAP, DUPLICATE, OUT_OF_ORDER, and RESET.
- Per-node connection counts, current values, ONLINE/SUSPECT/OFFLINE liveness, and timestamped
  transitions. Any valid reading or heartbeat restores ONLINE state.
- Separate counters for malformed JSON, schema rejection, rejected readings, oversized messages,
  truncated EOF, idle disconnects, and validated sensor traffic.
- Bounded asynchronous raw persistence with backpressure and off-event-loop file writes.
- RAW upstream mode: one collector record for every validated sensor reading.
- AGGREGATED upstream mode: configurable windows with reading/node counts and mean/min/max for
  temperature, humidity, and pressure. Final incomplete windows are explicitly marked partial.
- Reconnecting upstream delivery with explicit accounting if records must be abandoned during a
  bounded shutdown while the collector remains unavailable.
- Seeded virtual readings with baseline, slow drift, noise, reconnection, artificial delay, and
  application-message suppression controls in the Python API.
- One subprocess-orchestrated command for collector, gateway, and many independently connected
  virtual nodes over real localhost TCP connections.
- Exclusive per-run evidence directories with automatically captured Git/configuration/seed context.
- Persisted health transitions, gateway/forwarder events, node-side accounting, process/system
  metrics, component summaries, and logs.
- Warm-up-separated measurement intervals, deterministic repetitions, configurable
  failure/recovery, and application-level impairment.
- Analysis that preserves raw files, deduplicates collector records by `record_id`, derives metrics,
  rejects incompatible comparisons, and creates experiment-specific plots.

Synthetic readings are always labeled `"node_kind":"virtual"` and must not be presented as
physical measurements.

## Sensor protocol version 1

One compact JSON object followed by `\n` is one application message. TCP may split one message
across reads or combine several messages into one read.

```json
{
  "type": "reading",
  "version": 1,
  "node_id": "virtual-001",
  "node_kind": "virtual",
  "sequence": 0,
  "timestamp_ms": 1786482001123,
  "temperature_c": 23.82,
  "humidity_pct": 47.31,
  "pressure_hpa": 1012.4
}
```

Heartbeat messages use the identity, sequence, and timestamp fields with `"type":"heartbeat"`
and no sensor payload. Sequence gaps estimate generated-but-unreceived application messages;
duplicates and out-of-order arrivals are recorded separately.

All documented byte values are bytes at the NDJSON application boundary, including the newline.
They are not TCP/IP packet counts or physical-layer measurements.

## Requirements and setup

- Python 3.12 or newer (validated with Python 3.13.13)
- macOS or Linux for local development
- Git
- PlatformIO and ESP32/BME280 hardware are not required until Phase 5

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Substitute another Python 3.12+ executable if `python3.13` is unavailable.

## Run a reproducible experiment

One command starts an independent collector process, gateway process, and one simulator process
that owns the requested number of independently connected virtual nodes:

```bash
source .venv/bin/activate
python -m experiments.run \
  --experiment scaling \
  --nodes 50 \
  --duration 60 \
  --warmup 5 \
  --sampling-interval-ms 1000 \
  --aggregation raw \
  --seed 662
```

Every repetition receives a new directory under `results/raw/<run-id>/`; existing runs are never
overwritten. The runner chooses available localhost ports, captures the resolved ports and all
validated settings automatically, and writes:

```text
manifest.json             immutable configuration, Git, Python, and host context
readings.ndjson            validated gateway input with receive metadata
upstream.ndjson            collector input, including any at-least-once duplicates
health_events.csv          ONLINE/SUSPECT/OFFLINE transitions
gateway_events.csv         protocol, sequence, connection, and forwarder events
system_metrics.csv         sampled gateway CPU/RSS/VMS and host CPU
node_stats.csv             scheduled/generated/attempted/written/dropped counts
simulator_summary.json     run and measurement boundaries; failure/recovery times
gateway_summary.json       final gateway, registry, queue, event, and metrics counters
collector_summary.json     final collector counters
run_summary.json           overall status, child exits, config, and component summaries
*.log                      one diagnostic log per subprocess
```

Repeat trials with `--repetitions 3`. Seeds are deterministic: repetition `i` uses
`base_seed + i`, so a base of 662 produces 662, 663, and 664, each recorded in its manifest.

Supported experiment families are:

- `scaling`: vary `--nodes` (the planned matrix is 5, 10, 25, 50, 100).
- `aggregation`: use `--aggregation raw` or a positive window in seconds, such as
  `--aggregation 1`, `5`, or `10`.
- `failure`: set `--failure-at` and optional `--recovery-at`, relative to measurement start. If
  omitted, the runner uses one-third and two-thirds of the measurement duration.
- `impairment`: set `--drop-probability` and/or `--artificial-delay-ms`. These controls suppress or
  delay application messages; they do not emulate or measure Wi-Fi packet loss.

Warm-up traffic is preserved as raw evidence, but normal analysis uses only the recorded
`measurement_start_ms` through `measurement_end_ms` interval.

## Analyze immutable evidence

Analyze one run without modifying its raw files:

```bash
python -m analysis.analyze results/raw/<run-id>
```

Analyze compatible runs of one family and create supported comparison plots:

```bash
python -m analysis.analyze --experiment scaling
python -m analysis.analyze --experiment aggregation
```

Per-run derived data goes to `results/processed/<run-id>/`; comparisons go to
`results/processed/comparison-<experiment>/`; figures go to
`results/figures/<experiment>/`. Outputs use exclusive creation so an earlier analysis is not
silently replaced.

Analysis derives scheduled/generation/send/gateway reliability ratios, sequence status counts,
throughput, local virtual-node latency, gateway CPU/memory, unique collector messages and exact
NDJSON application bytes, RAW baselines and reductions, information delay, and failure/recovery
metrics. It deduplicates collector records by `record_id` only in processed data. It rejects absent
or unsupported manifests and incompatible comparison dimensions, and warns when evidence came from
a dirty Git tree or experienced forwarding-queue drops.

## Manual component operation

The orchestrated runner is preferred for evidence. The commands below remain useful for debugging
components individually.

### Run RAW mode end to end

Use a new directory or filenames for each run. Both services refuse to overwrite existing evidence.

Terminal 1 — collector:

```bash
source .venv/bin/activate
python -m collector.server \
  --host 127.0.0.1 \
  --port 9662 \
  --output results/raw/manual-raw-001/upstream.ndjson
```

Terminal 2 — gateway with raw persistence and RAW upstream forwarding:

```bash
source .venv/bin/activate
python -m gateway.server \
  --host 127.0.0.1 \
  --port 8662 \
  --raw-output results/raw/manual-raw-001/readings.ndjson \
  --upstream-mode raw \
  --collector-host 127.0.0.1 \
  --collector-port 9662 \
  --expected-interval 1 \
  --suspect-after 3 \
  --offline-after 5 \
  --liveness-check-interval 0.5
```

Terminal 3 — finite virtual node:

```bash
source .venv/bin/activate
python -m virtual_nodes.node \
  --node-id virtual-001 \
  --host 127.0.0.1 \
  --port 8662 \
  --interval 1 \
  --samples 5 \
  --seed 662
```

`--expected-interval` should match the normal node sampling interval. The default thresholds mark a
node SUSPECT after three expected intervals and OFFLINE after five. Keep the liveness-check interval
shorter than the time between those thresholds if both transitions must be observed.

### Run AGGREGATED mode

Start the collector as above with a new output path, then replace the gateway mode and output path:

```bash
source .venv/bin/activate
python -m gateway.server \
  --host 127.0.0.1 \
  --port 8662 \
  --raw-output results/raw/manual-aggregated-001/readings.ndjson \
  --upstream-mode aggregated \
  --collector-host 127.0.0.1 \
  --collector-port 9662 \
  --aggregation-window 5 \
  --expected-interval 1
```

Raw validated readings are still preserved at the gateway. Only collector-facing traffic is
aggregated, allowing direct calculation of upstream message and byte reduction.

Stop nodes first, then the gateway, then the collector with Ctrl-C. The gateway drains persistence
and upstream queues before exit. If the collector stays unavailable past the configured shutdown
timeout, abandonment is counted rather than silently ignored.

The CLI path was manually smoke-tested in both modes: RAW forwarded 3 readings as 3 collector
records; AGGREGATED preserved 4 source readings and forwarded 1 full-window summary. These were
engineering smoke tests in `/tmp`, not scientific experiment results.

## Test and quality checks

```bash
source .venv/bin/activate
python -m pytest -q
ruff check .
```

The current suite has 90 passing tests. Coverage includes protocol and framing edge cases, malformed
and abrupt clients, idle/size limits, callback isolation, concurrent virtual nodes, reconnection,
sequence and liveness transitions, exclusive persistence, aggregation statistics, collector
outage/recovery, bounded shutdown, event/metrics persistence, manifest Git-state capture, node-side
accounting, successful/failed/interrupted/repeated runs, analysis safeguards, and complete RAW and
AGGREGATED flows.

## Clock and latency methodology

Sensor messages carry a sender wall-clock timestamp. The gateway records wall-clock and monotonic
receive times at its validation boundary; the collector does the same at its boundary. Same-host
virtual-node tests can derive one-way application latency under a common wall clock. Gateway-local
durations and liveness use monotonic time.

Physical one-way latency will only be reported after explicit clock synchronization or will be
labeled approximate. Round-trip measurements are preferred where synchronized ESP32, Pi, and Mac
clocks cannot be verified. An aggregation record includes window start/end and forwarding time so
the aggregation delay is not hidden.

## Hardware and PlatformIO status

The planned physical deployment is three ESP32 nodes with BME280 sensors over Wi-Fi to a Raspberry
Pi gateway. `firmware/` remains an initialization placeholder and is not claimed as runnable.
Firmware will use PlatformIO/C++, never Arduino IDE. Local
`firmware/include/secrets.hpp` is ignored by Git; a documented example will be added with firmware.

## Experiments and results status

No scientific experiment matrix has been run and there are no findings yet. Short 10-, 50-, and
100-node RAW runs plus a matched 10-node RAW/aggregated pair have exercised the Phase 3 pipeline.
They are engineering smoke tests only: their 0.3-second measurement windows, same-host networking,
and dirty manifests are unsuitable for research conclusions. Generated data remains ignored by Git.

The scheduled denominator is defined independently as
`floor(measurement_duration / configured_interval)` per virtual node. Node statistics separately
record samples generated, sends attempted, application writes completed, application drops, and
gateway observations. This prevents a disconnected generator that pauses from making availability
look artificially perfect.

Sequence reset detection deliberately uses the smallest Phase 2 mechanism: a node incarnation must
begin at sequence `0`, and observing `0` after a higher value marks RESET. If that first post-reboot
application message is never observed, later low values remain OUT_OF_ORDER. TCP provides ordered,
reliable delivery after connection establishment, and future firmware must send sequence `0` first;
an explicit boot/incarnation identifier is deferred unless physical testing proves it necessary.

Other interpretation constraints:

- `estimated_messages_missing` is a gap estimate and does not shrink if a late message arrives.
- Aggregates are per reading, not per node. Use equal sampling rates when interpreting means.
- Duplicate readings remain in RAW evidence but are excluded from aggregate statistics. Collector
  evidence remains at-least-once and is deduplicated by `record_id` only during analysis.
- Empty aggregation windows are not emitted; collector silence does not mean zero sensor readings.
- Concurrent callbacks can make receive order within a window non-monotonic. Window timestamps are
  traffic boundaries, not environmental event-time claims.
- A repeated `node_id` can currently change `node_kind`; controlled runs must keep identity stable.
- Physical and virtual readings may share an aggregate. Do not publish environmental means that
  combine synthetic and physical measurements.
- Event persistence uses large bounded queues. Any event-row drops are exposed in the gateway
  summary rather than silently hidden.
- Gateway metrics describe the gateway process and host. They do not include collector or simulator
  process resource use.
- Application drop/delay controls are not Wi-Fi packet impairment. The current transport has no
  authentication or TLS and all automated runs are local-host TCP.
- Physical one-way latency remains unsupported until clock synchronization is verified.

## Repository structure

```text
gateway/
  protocol.py           sensor protocol models and NDJSON helpers
  server.py             bounded sensor-facing gateway and CLI
  registry.py           sequence, connection, and liveness state
  storage.py            asynchronous lossless raw persistence
  events.py             bounded structured event persistence
  metrics.py            asynchronous CPU and memory sampling
  aggregator.py         pure window aggregation
  upstream_protocol.py  RAW/AGGREGATED collector protocol
  upstream.py           queued reconnecting collector forwarder
collector/
  server.py             independent upstream collector and CLI
virtual_nodes/
  node.py               seeded synthetic sensor and CLI
  simulator.py          multi-node accounting and failure/recovery control
experiments/
  config.py             frozen validated experiment configuration
  manifest.py           immutable artifacts and automatic run context
  run.py                subprocess experiment orchestration
analysis/
  analyze.py            evidence loading, safeguards, and derived metrics
  plots.py              experiment-specific matplotlib figures
tests/                   unit and TCP integration tests
firmware/                Phase 5 placeholders
results/                 ignored raw/processed/figure outputs plus tracked markers
requirements.txt         pinned Python dependencies
pyproject.toml           pytest and Ruff configuration
```

## Current limitations and next milestone

Phase 3 is ready for a Raspberry Pi software deployment rehearsal: the gateway and runner are
configuration-driven, evidence is immutable, and local RAW/aggregated load smokes pass. It is not
yet validated on Raspberry Pi hardware, across real Wi-Fi, or against physical clocks and sensors.

The next milestone is a controlled Pi deployment and then ESP32/BME280 firmware using the existing
version-1 protocol. Hardware integration, secrets, clock methodology, and real network impairment
must be validated before the final experiment matrix. This repository does not yet claim that ESP32
firmware is implemented or that physical experimental results are available.
