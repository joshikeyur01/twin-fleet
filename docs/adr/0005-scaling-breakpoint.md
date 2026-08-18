# 5. The scaling breakpoint

Date: 2026-07-24
Status: Accepted

> Written **after** its evidence exists — the deliberate exception recorded in
> [ADR-0001](0001-record-architecture-decisions.md). Every number below comes
> from `just loadtest` runs on one machine (Apple M1 Pro, 16 GB), one Compose
> stack, synthetic simulators at 50 Hz. Raw per-run CSVs land in the gitignored
> `loadtest/runs/`; the tables here are the committed summary.

## Context

`twin-fleet` runs one Mosquitto, one InfluxDB, one instance of each of the five
services for the whole fleet. `twin-services` promised that "twin-fleet's load
test will find the ceiling on purpose" — this ADR is that promise paid. The ramp
(`loadtest/ramp.py`) scaled 1 → 20 robots, each publishing 6 joints × 3 fields ×
50 Hz ≈ 900 points/s, recording at each step: coordinated-command latency
p50/p95/p99, MQTT broker CPU %, and InfluxDB write throughput (points/s, from the
`twin_influx_points_written_total` counter the dashboard also reads).

## The breakpoint (baseline)

The first ceiling is the **InfluxDB write path in `telemetry-svc`**, and it is
hit at **N = 1**, not somewhere out at 20.

| N robots | Influx pts/s | cmd p50 | cmd p95 | cmd p99 | broker CPU % |
| -------- | ------------ | ------- | ------- | ------- | ------------ |
| 1        | 701          | 4.7 ms  | 10.3 ms | 15.9 ms | 1            |
| 2        | 807          | 5.3 ms  | 12.8 ms | 27.1 ms | 2            |
| 5        | 702          | 48.6 ms | 59.5 ms | 107.4 ms| 9            |
| 10       | 579          | 53.2 ms | 96.0 ms | 646.7 ms| 10           |
| 15       | 612          | 57.6 ms | 84.1 ms | 89.8 ms | 15           |
| 20       | 509          | 63.1 ms | 98.8 ms | 139.8 ms| 23           |

Read the throughput column: it is **flat at ~500–800 points/s and never rises
with N** — it even *drifts down*. One robot alone produces ~900 points/s, so even
at N=1 the service persists less than a single robot emits; at N=20, 18,000 pts/s
are produced and only ~509 land. The missing ~97% are QoS-0 telemetry the broker
drops to a slow consumer.

The cause is one line: `telemetry-svc` did `await write_api.write(record=point)`
**per message**. Each write is an HTTP round-trip to InfluxDB (~1.4 ms), and the
consume loop awaits it before taking the next message — so ingest serialises at
~700 points/s regardless of how many robots feed it. Adding robots does not add
throughput; it adds dropped samples.

Two secondary observations, deliberately *not* the headline:

- **Command latency has its own knee at N≈5** (p50 5→49 ms going 2→5 robots).
  This is fan-out contention (N concurrent `command-svc` calls, each a QoS-1
  publish competing with telemetry on the broker), independent of the write path.
- **Broker CPU is never the ceiling** — 23 % at N=20. The single Mosquitto has
  ample headroom.

## Decision

**Batch the InfluxDB writes**, and — with that fix — **keep the single-broker
Compose stack; do not adopt Kubernetes or a second broker.**

The fix (`telemetry-svc/ingest.py`): accumulate points and flush as one Influx
call when the buffer reaches 500 points **or** 0.5 s elapses (the timeout keeps
low-rate telemetry landing promptly). A failed flush drops its batch — bounded
loss keeps memory flat, honest for QoS-0 data.

## Results after the fix

| N robots | Influx pts/s | cmd p50 | cmd p95 | cmd p99 | broker CPU % |
| -------- | ------------ | ------- | ------- | ------- | ------------ |
| 1        | 623          | 8.3 ms  | 39.8 ms | 77.5 ms | 2            |
| 2        | 1 506        | 7.1 ms  | 18.7 ms | 27.9 ms | 3            |
| 5        | 4 050        | 49.1 ms | 58.0 ms | 61.7 ms | 6            |
| 10       | 7 833        | 55.4 ms | 98.2 ms | 113.8 ms| 12           |
| 15       | 11 439       | 59.4 ms | 93.1 ms | 182.2 ms| 46           |
| 20       | **17 978**   | 66.5 ms | 114.8 ms| 136.0 ms| 21           |

**The knee moved ~25×.** Throughput now scales **linearly** with N, and at N=20
`telemetry-svc` persists 17,978 pts/s ≈ the full 18,000 the fleet produces — no
drops. The write path is no longer the bottleneck anywhere on the ramp.

Given that, orchestration is **not** justified:

- The measured limiter was code (one `await` per point), not the broker, the
  database, or the host. Batching fixed it inside one service.
- After the fix, one Mosquitto + one InfluxDB + one `telemetry-svc` keep up with
  20 robots at 50 Hz with broker CPU ~20–46 % and p99 command latency ≤ ~180 ms.
- Kubernetes, a second broker, or per-robot service sharding would add real
  operational cost to buy headroom the numbers say we already have. This is the
  measured reason `VISION.md`/`AGENTS.md` deferred the K8s call to here — and the
  answer is **no, and here is the throughput curve that says so.**

## Consequences

- twin-fleet has a defensible capacity claim: **one host, one broker, one of each
  service holds 20 robots at 50 Hz (~18k points/s) after batching.**
- The next ceiling is now visible and named: the **command-latency knee at N≈5**
  (fan-out/broker contention), not the write path. Candidate fixes if it ever
  matters: raise the fan-out concurrency bound, or decouple command publishes
  from the telemetry-loaded broker. Left as future work — it does not gate the
  20-robot claim (p99 stays ≤ ~180 ms).
- The portfolio's K8s question is answered with evidence, not taste.

## Alternatives to the batch fix, considered

- **Coarser precision / fewer fields:** would cut volume but changes the data
  contract; rejected — the fix should not degrade fidelity.
- **A second `telemetry-svc` sharded by `robot_id` topic prefix** (possible
  because ADR-0002 put identity in the topic): a valid horizontal fix, but
  batching made it unnecessary at this scale — one service now does 18k pts/s.
  Kept in reserve for a future ceiling, not adopted now.

## Dependency notes

No new runtime dependency: batching is a buffer plus a flush trigger inside the
existing `telemetry-svc` ingest loop, using the same `influxdb-client` write API
(which already accepts a list of points as one call).
