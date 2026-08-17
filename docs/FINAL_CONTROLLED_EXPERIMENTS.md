# Frozen final controlled experiment design

## Research question and scope

How effectively can edge aggregation reduce upstream communication in a hybrid
physical/virtual wireless sensor network while preserving reliable sensing as the network scales
and experiences node failure?

The frozen three-node shower dataset is the physical application validation. It is not repeated and
is not treated as a controlled network-performance experiment. The controlled runs below use only
virtual nodes because the local orchestrator can start, identify, schedule, fail, and account for
them reproducibly. They must remain labeled `virtual` in every manifest and reading.

Application-loss and artificial-delay experiments are deferred. No degraded-network claim is
permitted unless a later reviewed experiment supplies that evidence.

## Duration and deterministic repetitions

- Warm-up: 60 seconds.
- Measurement: 300 seconds.
- Sampling interval: 1,000 ms.
- Repetitions: three per condition.
- Repetition seeds: 662, 663, and 664.
- Seed rule: `random_seed = 662 + zero-based repetition_index`.

The design has nine unique conditions and 27 unique executions. Nominal acquisition time is
`27 × (60 + 300) = 9,720 seconds = 162 minutes`. Allow approximately 165–175 minutes overall for
process startup, shutdown, gate evaluation, and per-run analysis. This is operationally reasonable,
so the preferred 60/300-second durations are retained.

## A. Scaling and 5-second edge aggregation

| Nodes | RAW repetitions | 5-second aggregate repetitions |
|---:|---:|---:|
| 5 | 3 | 3 |
| 25 | 3 | 3 |
| 100 | 3 | 3 |

This contributes 18 analytical runs. Primary outputs are gateway throughput, CPU/RSS, sensing
integrity, and actual collector NDJSON messages and bytes, including retransmissions.

## B. Aggregation-window tradeoff

At 25 nodes, compare RAW and 1-, 5-, and 10-second aggregation, with three repetitions per
condition. This is 12 analytical runs, but only six additional executions:

- Reuse the three A/25/RAW runs.
- Reuse the three A/25/5-second runs.
- Execute three 1-second and three 10-second runs.

Actual collector messages and bytes, including retransmissions, are primary. Unique logical records
are secondary. The gateway-window/collector timing difference is an information-delay metric for
the same-host software testbed, not physical one-way network latency.

## C. Failure and recovery

Run three 25-node RAW repetitions. `virtual-000` is the predetermined intervention node.

- Warm-up ends at measurement time 0 seconds.
- Failure request: measurement time 90 seconds.
- Recovery request: measurement time 180 seconds.
- Measurement end: 300 seconds.

Record the intervention timestamps, first `SUSPECT`, `OFFLINE`, recovery to `ONLINE`, healthy-peer
throughput and delivery integrity, and sequence behavior. Do not add an aggregated failure condition
unless the RAW evidence reveals a specific reviewed reason.

## Final-run admission

Every final run must originate from a clean tracked worktree and record the exact commit. The runner
refuses a `controlled_final` run when Git is dirty. Per-run analysis emits a
`scientific_admission` decision requiring:

- `run_summary.status == "success"` and all child return codes zero;
- clean run and analysis worktrees;
- complete component summaries and required raw files;
- no upstream queue-full drops or shutdown abandonment;
- no gateway, collector, or event-persistence invalid/drop counters;
- storage enqueue/write parity;
- gateway-to-collector application message and byte parity;
- a valid independent scheduled-reading delivery denominator.

Evidence that fails a gate is preserved and excluded. The automated campaign stops at the first
failed admission; rerun that condition under a new campaign identifier after diagnosing it. Never
overwrite the failed run.

Comparison analysis must additionally enforce equal warm-up, measurement duration, sampling and
metrics cadence, liveness thresholds, queue sizes, impairment settings, and all other dimensions not
named as the independent variable.

## Automation

Inspect the frozen plan without creating evidence:

```bash
.venv/bin/python -m experiments.final_matrix
```

After the final review, execute all 27 unique runs:

```bash
.venv/bin/python -m experiments.final_matrix \
  --campaign-id final-controlled-v1 \
  --warmup 60 \
  --duration 300 \
  --seed 662 \
  --execute
```

The campaign plan, every condition/repetition/seed, raw evidence, per-run metrics, and admission
results are written exclusively below `results/raw/final-controlled-v1/` and
`results/processed/final-controlled-v1/`. Existing directories cause refusal rather than reuse.

Stop before executing this command until the design and engineering rehearsals receive a final GO.
