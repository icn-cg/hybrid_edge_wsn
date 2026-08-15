# Hybrid Edge Wireless Sensor Network

A graduate Wireless Sensor Networks project for experimentally studying how edge aggregation
changes bandwidth use, latency, and reliability as a hybrid network scales and communication
degrades.

The current implementation is **Phase 2: edge behavior**. It provides a versioned sensor protocol,
a concurrent gateway, per-node sequence and health tracking, lossless raw persistence, windowed
aggregation, a reconnecting upstream forwarder, an independent collector, seeded virtual nodes,
and automated end-to-end tests. Experiment orchestration, analysis, and ESP32 firmware remain
future phases.

## Research question

> How does edge aggregation affect bandwidth usage, latency, and reliability as a hybrid
> wireless sensor network scales and experiences degraded network conditions?

Secondary measurements planned for Phase 3 include gateway CPU/memory use, aggregation-window
tradeoffs, failure/recovery detection time, and controlled communication impairment.

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

## Run RAW mode end to end

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

## Run AGGREGATED mode

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

The current suite has 63 passing tests. Coverage includes protocol edge cases, framing boundaries,
malformed and abrupt clients, idle/size limits, callback isolation, 10 concurrent virtual nodes,
reconnection, sequence classification, liveness transitions, exclusive raw persistence,
aggregation statistics/window behavior, collector outage/recovery, bounded shutdown, and complete
virtual-node → gateway → collector RAW and AGGREGATED flows.

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

No scientific experiments have been run and there are no findings yet. Phase 3 will add a
configuration-driven runner that creates one immutable directory per run, preserving configuration,
seed, Git commit, readings, upstream records, node events, gateway events, and system metrics.
Analysis will consume those files directly.

Application-message suppression is not physical Wi-Fi packet loss. A virtual node currently pauses
generation while disconnected, so availability during connection outages must be derived from the
configured rate and elapsed time rather than only `received / generated`. Physical and synthetic
measurements will remain explicitly separated.

## Repository structure

```text
gateway/
  protocol.py           sensor protocol models and NDJSON helpers
  server.py             bounded sensor-facing gateway and CLI
  registry.py           sequence, connection, and liveness state
  storage.py            asynchronous lossless raw persistence
  aggregator.py         pure window aggregation
  upstream_protocol.py  RAW/AGGREGATED collector protocol
  upstream.py           queued reconnecting collector forwarder
collector/
  server.py             independent upstream collector and CLI
virtual_nodes/
  node.py               seeded synthetic sensor and CLI
tests/                   unit and TCP integration tests
firmware/                Phase 5 placeholders
experiments/             Phase 3 placeholders
analysis/                Phase 3 placeholders
results/                 future experiment evidence and derived outputs
requirements.txt         pinned Python dependencies
pyproject.toml           pytest and Ruff configuration
```

## Current limitations and next milestone

Phase 2 has no authentication/TLS, experiment manifest, system-resource sampler, automatic repeated
trials, analysis pipeline, or physical firmware. Registry and transition state are currently held in
memory; Phase 3 will persist transition/event streams as part of each configured run. Upstream
delivery reconnects with at-least-once intent, so record IDs are included to reveal possible
retransmission duplicates rather than hiding them.

The next milestone is Phase 3: configuration-driven experiments, immutable run directories,
system metrics, repeated runs, and automated analysis. Firmware and Raspberry Pi deployment should
wait until that local experiment pipeline is reproducible.
