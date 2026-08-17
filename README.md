# Hybrid Edge Wireless Sensor Network

A graduate Wireless Sensor Networks project for experimentally studying how edge aggregation
changes bandwidth use, latency, and reliability as a hybrid network scales and communication
degrades.

The current implementation is **Phase 4: physical-node ESP32 bring-up**. It provides a
versioned sensor protocol, concurrent gateway and collector, immutable run manifests, persisted
health/gateway events, independently accounted virtual generation, CPU/memory sampling,
subprocess orchestration, evidence-driven analysis, a real Raspberry Pi software rehearsal, and
host-tested ESP32/BME280 firmware logic. Three ESP32 boards have passed USB flash, Serial boot,
Wi-Fi association, and DHCP; the initial board is quarantined for a repeatable Wi-Fi/RF failure.
A pre-soldered BME280 connected to ESP32-B has passed local I2C/Serial validation, physical RAW
persistence on the Pi, and bounded end-to-end forwarding to the Mac collector.

## Research question

> How does edge aggregation affect bandwidth usage, latency, and reliability as a hybrid
> wireless sensor network scales and experiences degraded network conditions?

The testbed can measure gateway CPU/memory use, aggregation-window tradeoffs, failure/recovery
detection time, and controlled application-level impairment. No final scientific experiment matrix
or conclusions have been produced.

## Architecture

```text
Physical node (ESP32-B + BME280 validated)     Mac upstream collector
BME280 -> ESP32 -- Wi-Fi/TCP --+                         ^
                               |                         | TCP/NDJSON
Virtual nodes -- TCP ----------+-> Raspberry Pi gateway-+
       (implemented)                    (wsn-edge)
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
- Real Mac → Raspberry Pi → Mac RAW and AGGREGATED engineering rehearsals over LAN TCP.
- Board-independent physical-node NDJSON, validation, sequence, and reconnect-backoff logic with
  host tests, plus a target-built Arduino BME280/Wi-Fi/persistent-TCP state machine for a classic
  ESP32 DevKit V1 (`esp32doit-devkit-v1`).

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

- Python 3.12 or newer (validated with Python 3.13.13 on Mac and 3.13.5 on Raspberry Pi)
- macOS or Linux for local development
- Git
- PlatformIO is required for Phase 4 firmware tests/builds; the Python system does not require it

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
silently replaced. Experiment-level analysis refuses to reuse an existing per-run processed
directory; use a fresh `--processed-root` when reanalyzing with newer code.

Analysis derives scheduled/generation/send/gateway reliability ratios, sequence status counts,
throughput, local virtual-node latency, gateway CPU/memory, unique collector messages and exact
NDJSON application bytes, RAW baselines and reductions, information delay, and failure/recovery
metrics. Actual collector messages and bytes—including retransmissions—are the primary upstream
network-efficiency metrics; deduplicated values are retained as logical-record metrics. Conflicting
payloads under one `record_id` are rejected.

Analysis rejects absent, failed, interrupted, schema-incompatible, or internally inconsistent runs.
`delivery_ratio` uses non-duplicate virtual readings received in the measurement receive window over
the independent scheduled denominator. If boundary leakage or accounting could make that value
exceed one, analysis emits `null`, records the invalidation reason, and plotting excludes it. A
comparison also rejects dirty run manifests, a dirty analysis worktree, incompatible liveness or
metrics sampling cadences, and stale processed outputs. `--allow-dirty` exists only for explicitly
labeled engineering comparisons.

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
pio --version
pio test -d firmware -e native
pio run -d firmware -e physical-node
pio run -d firmware -e physical-node-001
pio run -d firmware -e physical-node-002
pio run -d firmware -e physical-node-003
python -m pytest -q
ruff check .
git diff --check
```

The native environment is test-only: use `pio test`, not `pio run`, for `-e native`. The current
Python suite has 104 passing tests. Coverage includes firmware-emitter compatibility, protocol and
framing edge cases,
malformed and abrupt clients, idle/size limits, callback isolation, concurrent virtual nodes,
reconnection, sequence and liveness transitions, exclusive persistence, aggregation statistics,
collector outage/recovery, bounded shutdown, event/metrics persistence, manifest Git-state capture,
node-side accounting, successful/failed/interrupted/repeated runs, analysis safeguards, and complete
RAW and AGGREGATED flows.

## Clock and latency methodology

Virtual sensor messages carry a sender wall-clock timestamp. The Phase 4 physical firmware instead
uses monotonic milliseconds since ESP32 boot because NTP and clock synchronization are not yet part
of the system. The gateway records wall-clock and monotonic receive times at its validation boundary;
the collector does the same at its boundary. Same-host virtual-node tests can derive one-way
application latency under a common wall clock. Gateway-local durations and liveness use monotonic
time.

Physical one-way latency will not be reported until explicit clock synchronization is implemented
and verified. Round-trip measurements are preferred where synchronized ESP32, Pi, and Mac clocks
cannot be verified. An aggregation record includes window start/end and forwarding time so the
aggregation delay is not hidden.

## Hardware and PlatformIO status

Phase 4 evaluated four classic ESP32 DevKit V1 boards with CP2102 USB-to-UART bridges. ESP32-A is
quarantined from Wi-Fi use; ESP32-B is the primary physical-node candidate; ESP32-C and ESP32-D are
validated spares. PlatformIO board `esp32doit-devkit-v1` builds with Arduino and GNU C++17.
Firmware explicitly assigns GPIO21 as SDA and GPIO22 as SCL, probes BME280 addresses `0x76` then
`0x77`, and provides explicit build profiles for `physical-001`, `physical-002`, and
`physical-003`. See [firmware/README.md](firmware/README.md) for the full contract, planned wiring,
profile-to-board map, and checkpoint procedure. The complete physical device registry is maintained in
[firmware/HARDWARE_INVENTORY.md](firmware/HARDWARE_INVENTORY.md).

The HW-611 BME280 has an unsoldered header and must not be used. Its replacement pre-soldered module
is connected to ESP32-B and has passed local bring-up at I2C address `0x76`. The separate pre-sensor
checkpoint remains a sensor-absent transport procedure and must never produce a sensor reading or a
`physical-001` registry observation.

### Pre-sensor ESP32/TCP checkpoint

Activate the development environment, confirm PlatformIO, and compare the device list before and
after attaching a known data-capable USB-C cable:

```bash
source .venv/bin/activate
pio --version
pio device list
system_profiler SPUSBDataType
ls /dev/cu.*
```

Use the new CP2102 serial path reported by `pio device list`; do not assume its macOS name. If USB
shows the CP2102 but no `/dev/cu.*` device appears, rule out the cable/adapter before considering the
current Silicon Labs CP210x VCP driver.

Create the ignored credential file locally and edit only that copy:

```bash
cp firmware/include/secrets.example.hpp firmware/include/secrets.hpp
```

Never commit `secrets.hpp`. Confirm that `wsn-edge` still has DHCP address `192.168.1.187` and its
gateway is listening on port 8662, then select the profile matching the attached board, build,
upload, and monitor with the detected port. For ESP32-B:

```bash
pio run -d firmware -e physical-node-001
pio run -d firmware -e physical-node-001 -t upload --upload-port <PORT>
pio device monitor -d firmware --port <PORT> --baud 115200
```

Use `physical-node-002` only for ESP32-C (MAC ending `2a:54`) and `physical-node-003` only for
ESP32-D (MAC ending `3d:a8`). The original `physical-node` environment remains an alias for
ESP32-B/`physical-001`. Confirm the station MAC before each upload.

Expected Serial behavior is: boot at 115200, print `physical-001` and the Pi target, initialize I2C
on GPIO21/GPIO22, probe `0x76` and `0x77`, report the BME280 absent, and repeat the probe every five
seconds. With valid local credentials it should also join Wi-Fi, print its DHCP-assigned ESP32 IP,
and connect to `192.168.1.187:8662`. It must print no `Sample`, `TX`, or JSON reading.

Because the gateway creates a registry entry only after a valid application message, a sensor-free
TCP connection does not create `physical-001`. The Pi may accept the socket and close it after its
approximately 30-second idle timeout; firmware TCP reconnects are expected in this mode and are not
a crash or reboot. The exact fresh-path Pi command, expected zero-record summary, and gateway
stop/restart recovery procedure are documented in
[firmware/README.md](firmware/README.md#pre-sensor-tcp-rehearsal-on-the-pi).

### BME280 bring-up

With ESP32 power disconnected, wire the received replacement at 3.3 V: 3V3→VCC, GND→GND,
GPIO22→SCL,
GPIO21→SDA, 3V3→CSB, and GND→SDO (address `0x76`). After reboot, require detection, a finite and
plausible temperature/humidity/pressure sample, then validate sequences `0`, `1`, `2`, ... through
the Pi and Mac collector in RAW mode. Only after that should failure/recovery and aggregation checks
proceed.

The local portion passed on ESP32-B on 2026-08-16: the firmware detected the BME280 at `0x76` and
reported stable finite samples near 25 °C, 49% relative humidity, and 1011 hPa. A subsequent
engineering check persisted all 463 accepted physical RAW records on the Pi. The final summary
reported zero invalid, duplicate, out-of-order, or estimated-missing messages and correctly
recognized one deliberate ESP32 sequence reset. A subsequent end-to-end RAW rehearsal persisted
and forwarded 812 records on the Pi and collected all 812 on the Mac, with matching 361,242
upstream application bytes and no invalid, overlong, or truncated messages.

## Experiments and results status

No scientific experiment matrix has been run and there are no findings yet. Short 10-, 50-, and
100-node RAW runs plus a matched 10-node RAW/aggregated pair have exercised the Phase 3 pipeline.
They are engineering smoke tests only: their 0.3-second measurement windows, same-host networking,
and dirty manifests are unsuitable for research conclusions. Generated data remains ignored by Git.

The scheduled denominator is defined independently as
`floor(measurement_duration / configured_interval)` per virtual node. Node statistics separately
record samples generated, sends attempted, application writes completed, application drops, and
gateway observations. This prevents a disconnected generator that pauses from making availability
look artificially perfect. Because virtual generators free-run through warm-up, short windows can
have a phase-boundary mismatch; analysis invalidates rather than publishes ratios above one. The
0.3-second smoke delivery values are not suitable scientific measurements.

A real multi-machine software rehearsal has also completed over the LAN: Mac virtual node →
Raspberry Pi `wsn-edge` gateway → Mac collector. RAW preserved and forwarded 10 of 10 readings with
4,405 matching application bytes; AGGREGATED preserved 10 readings and forwarded two aggregates
with 1,063 matching application bytes. Both had zero queue drops, send failures, or abandonment, and
the RAW rehearsal observed ONLINE → SUSPECT → OFFLINE. These validate deployment plumbing only and
are not scientific results or wireless-performance measurements.

Sequence reset detection deliberately uses the smallest Phase 2 mechanism: a node incarnation must
begin at sequence `0`, and observing `0` after a higher value marks RESET. If that first post-reboot
application message is never observed, later low values remain OUT_OF_ORDER. TCP provides ordered,
reliable delivery after connection establishment, and the prepared firmware preserves sequence `0`
until its first complete local write; an explicit boot/incarnation identifier is deferred unless
physical testing proves it necessary.

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
- Failure injection currently targets `virtual-000`. Its timestamp is captured when cancellation is
  requested; analysis reports first-SUSPECT and OFFLINE threshold delays separately, with SUSPECT as
  the primary first-detection metric.
- Application drop/delay controls are not Wi-Fi packet impairment. The current transport has no
  authentication or TLS; configured experiment runs remain local-host TCP, while the separate Pi
  rehearsal validated only LAN deployment plumbing.
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
firmware/                Phase 4 one-node ESP32 firmware, host tests, and bring-up guide
results/                 ignored raw/processed/figure outputs plus tracked markers
requirements.txt         pinned Python dependencies
pyproject.toml           pytest and Ruff configuration
```

## Current limitations and next milestone

The Raspberry Pi software path is validated as an engineering rehearsal, and ESP32-B, ESP32-C, and
ESP32-D have passed flash and Wi-Fi/DHCP bring-up. ESP32-B plus the replacement BME280 has also
passed local I2C/Serial validation and an end-to-end RAW rehearsal in which the Pi persisted and
forwarded 812 records and the Mac collected all 812. Explicit build profiles now reserve ESP32-B as
`physical-001`, ESP32-C as `physical-002`, and ESP32-D as `physical-003`; the latter two images are
not yet flashed and neither spare has a BME280. The next action is to upload and verify those unique
profiles one board at a time before concurrent physical-node use. Clock synchronization, real
network impairment, additional sensors, and the final experiment matrix remain later work. This
repository does not yet claim a physical performance result or physical one-way latency.
