# Architecture

## The 5-layer stack

Same vocabulary as every `twin-*` repo. This repo's contribution is **breadth,
not a new layer**: the `twin-services` L4 becomes fleet-aware and gains one
coordination service (`fleet-svc`), while L1 multiplies to N synthetic UR5s.
L3 stays empty until `twin-aas`.

```
┌────────────────────────────────────────────────────────────────────────┐
│ L5  Application         Grafana (per-robot templated rows) · viz-svc    │
├────────────────────────────────────────────────────────────────────────┤
│ L4  Services            telemetry-svc · state-svc · command-svc ·       │
│                         fleet-svc  (registry + coordinated commands)     │
├────────────────────────────────────────────────────────────────────────┤
│ L3  Information model   (none — raw namespaced topics; added in twin-aas)│
├────────────────────────────────────────────────────────────────────────┤
│ L2  Transport           namespaced MQTT (twin/<robot_id>/…) · gRPC       │
├────────────────────────────────────────────────────────────────────────┤
│ L1  Physical asset      N × synthetic UR5 simulators (one per robot_id)  │
└────────────────────────────────────────────────────────────────────────┘
```

The only genuinely new box is `fleet-svc`. Everything else is a service that
learned to route by `robot_id`.

## Service topology

```
                                browser
                               ▲       ▲
                     WebSocket │       │ HTTP (templated dashboard)
                       ┌───────┴──┐  ┌─┴───────┐   ┌────────────┐
                       │ viz-svc  │  │ Grafana │◀──│ Prometheus │──/metrics──▶ all svcs
                       └───────┬──┘  └─┬───────┘   └────────────┘
                          gRPC │       │ Flux
                       ┌───────▼──┐  ┌─▼────────┐
   GET /fleet          │state-svc │  │ InfluxDB │
   POST /fleet/command │ (per-    │  └─▲────────┘
        ▲              │  robot   │    │ writes
        │              │  window) │  ┌─┴──────────────┐
   ┌────┴─────┐        └───────┬──┘  │ telemetry-svc  │  (subscribes twin/+/ur5/#,
   │ fleet-svc│        MQTT ────┤     └─▲──────────────┘   tags points by robot_id)
   │ registry │◀── passive ─────┤       │ MQTT telemetry
   │ + fan-out│    discovery    │       │
   └────┬─────┘  (twin/+/ur5/#) │  ┌────┴───────────────┐   ┌─────────────┐
        │ per-robot REST        └──│     Mosquitto      │◀──│ command-svc │◀── REST
        │ (composition)            └────▲───────────┬───┘   └──────┬──────┘   (+robot_id)
        └──────────────────────────────┘           │              │
                                    twin/<id>/ur5/  │  twin/<id>/ur5/cmd/joints
                                    joint/…         │              │
                          ┌──────────────────┬──────┴──────┬───────┘
                          │ robot_1 sim      │ robot_2 sim │  …  robot_N sim
                          │ (synthetic UR5)  │             │
                          └──────────────────┴─────────────┘
```

`fleet-svc` is the one new inhabitant. It is **not** on the telemetry write
path (that stays `telemetry-svc`) and **not** on the command publish path (that
stays `command-svc`). It composes: it *discovers* twins from the MQTT stream and
*fans out* a coordinated command across `command-svc`, exactly the "client-side
composition, not a new coupling" rule `twin-services` established.

## Namespacing

One robot had no identity; N robots need exactly one. The identity is
`robot_id` (e.g. `robot_1`), and it appears in **two** places on purpose:

- **In the MQTT topic**, for routing and subscription:
  `twin/<robot_id>/ur5/joint/<name>/<field>` and
  `twin/<robot_id>/ur5/cmd/joints`. This mirrors ROS 2 namespacing
  (`/robot_1/joint_states`) — the substitution is topic-prefix for
  node-namespace, one-for-one. Services subscribe with the wildcard
  `twin/+/ur5/#` and extract `robot_id` from the topic segment.
- **In the payload envelope**, as an additive field, so a message is
  self-describing even off the wire (logs, InfluxDB tags, replay files). The
  service cross-checks topic `robot_id` against envelope `robot_id`; a mismatch
  is a validation failure, not a silent accept.

Topic-*and*-payload (rather than one or the other) is [ADR-0002](../adr/0002-robot-namespacing.md).
The short version: the topic is how you *route*, the envelope is how you
*verify*, and at twenty robots you need both.

## Data flows

**Namespaced telemetry (the `twin-services` path, now × N):**

1. Each synthetic simulator publishes UR5-shaped `JointTelemetry` at 50 Hz to
   its own topic tree `twin/<robot_id>/ur5/joint/<name>/<field>`.
2. `telemetry-svc` subscribes once to `twin/+/ur5/#`, validates each payload,
   and writes to InfluxDB **tagged by `robot_id`** — one measurement, N series.
   No per-robot subscriber, no per-robot config: one subscriber, wildcard topic,
   tag on write.

**Per-robot derived state:**

3. `state-svc` maintains one rolling window **per `robot_id`**, computes
   end-effector pose (UR5 FK) and velocity RMS for each, and serves them over
   gRPC. `state.proto` gains a `robot_id` field (additive): `GetState(robot_id)`
   returns one twin's state; `StreamState` streams all live twins, each frame
   tagged.

**Coordinated command (the new fan-out path):**

4. A client `POST`s a fleet command to `fleet-svc`
   (`POST /fleet/command {"kind":"home"}`).
5. `fleet-svc` reads its registry of live twins, then issues one typed command
   per robot to `command-svc` (which now takes `robot_id` and publishes to
   `twin/<robot_id>/ur5/cmd/joints`). It aggregates the per-robot results and
   returns them — a dead twin appears as a failed entry, never a silent drop.
   This is [ADR-0004](../adr/0004-coordinated-commands.md): best-effort fan-out
   with per-robot accounting, not a distributed transaction.

**Registry / discovery:**

6. `fleet-svc` discovers twins **passively**: it subscribes to `twin/+/ur5/#`,
   records last-seen per `robot_id`, and marks a twin dead when it misses the
   liveness window (with MQTT Last-Will as the fast-path death signal).
   `GET /fleet` returns the live set. No robot registration protocol — the
   registry is derived from the telemetry that already flows. Passive-discovery
   vs explicit-registration is [ADR-0003](../adr/0003-fleet-registry.md).

**Load / capacity:**

7. `loadtest/` ramps 1 → 20 simulators, driving synthetic telemetry and periodic
   coordinated commands, while recording p50/p95/p99 command latency, MQTT
   broker CPU, and InfluxDB write throughput. The ramp runs until a metric knees;
   that knee and its fix are [ADR-0005](../adr/0005-scaling-breakpoint.md) — the
   repo's headline deliverable.

**Observability:**

8. Prometheus scrapes `/metrics` from all five services (each exposing per-robot
   labels where meaningful). Grafana renders one **templated row per robot** via
   a `robot_id` dashboard variable, so the view scales with the fleet with zero
   dashboard edits.

## Contracts

`contracts/` is inherited from `twin-services` and extended **additively only**
(per its own ADR-0003 schema-evolution rule):

- `JointTelemetry` / `JointCommand` envelopes gain a required `robot_id`
  (`schema_version` bumped; a `contracts/` CHANGELOG entry is mandatory).
- `state.proto` gains a `robot_id` field on `TwinState` and a `robot_id`
  request field on `GetState`; field numbers are appended, never reused;
  regenerated stubs are checked in under `contracts/gen/`.
- A new `FleetCommand` / `FleetCommandResult` pair (Pydantic) describes the
  coordinated-command request and the per-robot result list.

Rule, still CI-enforced: a service that declares a payload shape locally fails
review. `fleet-svc` importing `contracts` is the only acceptable source of the
fleet-command shape.

## Ports

| Component      | Port          | Protocol                        |
| -------------- | ------------- | ------------------------------- |
| Mosquitto      | 1883          | MQTT                            |
| InfluxDB       | 8086          | HTTP                            |
| Grafana        | 3000          | HTTP                            |
| Prometheus     | 9090          | HTTP                            |
| telemetry-svc  | 8001          | HTTP (healthz/metrics)          |
| state-svc      | 8002 / 50051  | HTTP / gRPC                     |
| command-svc    | 8003          | HTTP (REST + healthz/metrics)   |
| viz-svc        | 8004          | HTTP + WebSocket                |
| **fleet-svc**  | **8005**      | **HTTP (REST + healthz/metrics)** |

The N simulators are internal processes, not ports — they publish to Mosquitto
and expose nothing. One broker, one InfluxDB, one Grafana serve the whole fleet;
that single-broker assumption is precisely what the load test is built to break.

## Design decisions (summaries — the ADRs argue them)

### Robot namespacing: topic *and* payload — [ADR-0002](../adr/0002-robot-namespacing.md)

`robot_id` lives in both the MQTT topic (routing) and the envelope
(verification). Services subscribe with `twin/+/ur5/#` and cross-check. Mirrors
ROS 2 node namespacing at the topic layer.

### Fleet registry by passive discovery — [ADR-0003](../adr/0003-fleet-registry.md)

`fleet-svc` derives the live set from the telemetry stream it already sees,
plus MQTT Last-Will for fast death detection — no separate registration RPC.
The trade-off (discovery latency vs protocol simplicity) is argued with the
measured last-seen window, not asserted.

### Coordinated commands: best-effort fan-out — [ADR-0004](../adr/0004-coordinated-commands.md)

"All robots home" is N independent commands with per-robot accounting, not an
atomic transaction. A dead twin fails its own entry; the others still move. Why
not two-phase commit: the honest answer, with the failure modes each choice
accepts.

### The scaling breakpoint — [ADR-0005](../adr/0005-scaling-breakpoint.md)

Written **after** the load test, not before. Names the metric that knees first
(candidate: broker CPU or InfluxDB write throughput), where the knee is (N and
Hz), why, and the fix — or the reasoned decision that Compose-scaled single-broker
is sufficient for the thesis and orchestration stays out. This is the ADR the
whole repo exists to write.

## What this repo intentionally omits

- **Real ROS 2 / Gazebo multi-robot.** Synthetic simulators publish namespaced
  telemetry; physics fidelity is `twin-cubesat`'s job.
- **Kubernetes / service mesh — until measured.** Adopted only if ADR-0005's
  breakpoint demands it. A single Compose-scaled Mosquitto is the baseline the
  load test attacks; if it holds 20 robots, K8s stays out and the ADR says so.
- **Per-robot service instances.** One `telemetry-svc`, one `state-svc`, one
  `command-svc` serve the whole fleet by routing on `robot_id`. Sharding a
  service by robot is a *fix the load test might justify*, recorded in ADR-0005 —
  not a starting assumption.
- **mTLS / per-tenant isolation.** Noted at each boundary; one trust domain,
  localhost. Real multi-tenancy is beyond the scaling argument.
- **Cross-robot analytics or ML.** Fleet-wide anomaly detection reuses
  `twin-anomaly`; it does not grow a sixth service here.
- **Backpressure / replay durability.** Fire-and-forget MQTT is kept on purpose
  so the load test can find where it stops surviving. The ceiling is data, not a
  bug to pre-emptively engineer around.
