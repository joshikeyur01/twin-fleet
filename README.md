# twin-fleet

> The fleet-scale digital twin: the `twin-services` stack generalised from one
> UR5 to **N**, coordinated through a registry, and ramped 1 → 20 until
> something bends — so an ADR can name the breakpoint with a number. Fifth rung
> of the [`twin-*`](https://github.com/joshikeyur01?tab=repositories&q=twin-)
> portfolio.

![demo](docs/demo/twin-fleet.gif)

## What this is

`twin-services` proved the architecture for one robot; this repo proves it
**scales — and finds where it stops.** The same four services learn to route by
`robot_id`, a fifth service (**fleet-svc**) keeps a live registry and fans a
coordinated command (`all robots home`) out across the fleet, Grafana grows a
row per robot with no dashboard edit, and a load test ramps 1 → 20 robots
recording p50/p95/p99 command latency, MQTT broker CPU, and InfluxDB write
throughput until a metric knees.

The headline deliverable is one ADR: **the observed scaling breakpoint and the
fix** — `twin-services`' promise that "twin-fleet's load test will find the
ceiling on purpose," paid.

**Honest constraint:** there is no ROS 2 / Gazebo on this machine (as in every
`twin-*` repo so far). N robots are N *synthetic* UR5 simulators publishing
namespaced telemetry. The scaling proof holds because the broker and database
see identical bytes whether the joint stream came from Gazebo or from numpy —
see [`docs/context/VISION.md`](docs/context/VISION.md).

Deliberately does **not** include: real multi-robot physics (`twin-cubesat`),
cross-robot ML (`twin-anomaly`), semantic models (`twin-aas`), or Kubernetes
(only if ADR-0005's measured breakpoint demands it — see below).

## Architecture (5-layer stack)

| Layer | Component |
|-------|-----------|
| L5 Application | Grafana (per-robot templated rows) · viz-svc (React + r3f) |
| L4 Services | telemetry-svc · state-svc · command-svc · **fleet-svc** |
| L3 Information model | *(none — raw namespaced topics; added in `twin-aas`)* |
| L2 Transport | namespaced MQTT (`twin/<robot_id>/…`) · gRPC (svc↔svc) |
| L1 Physical / simulated | N × synthetic UR5 (one per `robot_id`) |

See [`docs/context/ARCHITECTURE.md`](docs/context/ARCHITECTURE.md) and the ADRs
in [`docs/adr/`](docs/adr/) — especially 0002 (namespacing), 0003 (registry),
0004 (coordinated commands), 0005 (the breakpoint).

## Quick start

Prerequisites: Docker, Docker Compose, [`just`](https://github.com/casey/just),
[`uv`](https://docs.astral.sh/uv/).

```bash
just fleet 20    # build + start infra, 5 services, and 20 synthetic robots
just healthz       # infra + 5 services green
just loadtest      # ramp 1→20; record p50/p95/p99, broker CPU, Influx throughput
```

Inspect and coordinate the fleet:

```bash
curl localhost:8005/fleet                                   # live twins + last-seen
curl -X POST localhost:8005/fleet/command \
     -H 'content-type: application/json' \
     -d '{"kind":"home"}'      # moves every live robot; dead ones report as failed
```

Then open <http://localhost:3000> (Grafana: one templated row per robot + a
fleet-status timeline). Add or remove robots and the dashboard rescales itself.

## Repo layout

```
contracts/            # source of truth: Pydantic + proto + stubs (now robot_id-keyed)
services/
  telemetry-svc/      # twin/+/ur5/# → validate → InfluxDB, tagged by robot_id
  state-svc/          # per-robot window → FK → gRPC GetState(robot_id)/StreamState
  command-svc/        # POST /command (+robot_id) → namespaced MQTT setpoint
  viz-svc/            # serves the viewer; StreamState → WebSocket
  fleet-svc/          # passive-discovery registry + coordinated-command fan-out
simulators/           # N synthetic UR5s, one process per robot_id
loadtest/             # 1→20 ramp harness; emits the breakpoint table
deploy/               # mosquitto, prometheus, grafana (templated per-robot rows)
tests/integration/    # multi-robot + kill-one-of-N against the compose stack
docs/context/         # vision, architecture, style, roadmap
docs/adr/             # decisions with evidence — 0005 is the headline
```

## What I learned

The honest list lives in [`WHAT_I_LEARNED.md`](WHAT_I_LEARNED.md) — including
where the single-broker assumption actually broke, which metric kneed first, and
whether Kubernetes turned out to be justified or just tempting.

## Licence

Apache-2.0 — see [`LICENSE`](LICENSE).
