# Project context & conventions

Read this before touching code. It sets the architecture, conventions, and
guardrails for any work in this repository. `twin-fleet` **forks `twin-services`** —
when in doubt, the seam should match upstream, because `twin-cubesat` forks this
in turn.

## Mission

The fleet-scale digital twin: the `twin-services` stack generalised from one UR5
to **N**, keyed by `robot_id`, with a coordination service (`fleet-svc`) and a
load test built to find where the single-broker architecture breaks.

Success criterion, two halves:

1. `just fleet 20` runs twenty namespaced twins; `GET /fleet` lists them;
   `POST /fleet/command {"kind":"home"}` moves all live robots; a killed robot
   leaves `GET /fleet` rather than lingering as a phantom success, and a genuine
   dispatch failure (command-svc/broker down) is a failed entry, never a fake
   200 (ADR-0004); the Grafana dashboard grows a row per robot with no edit.
2. **ADR-0005 names the scaling breakpoint** — the metric that knees first, the
   N/Hz where, why, and the fix (or the numbers-backed decision that
   single-broker Compose suffices). This ADR is the reason the repo exists.

## Stack

Python 3.12 · Mosquitto (MQTT) · InfluxDB 2 · Grafana · Prometheus ·
gRPC/protobuf · React + react-three-fiber · Docker Compose · `uv` (workspace) ·
`just`. **No ROS 2 / Gazebo on this machine** — N robots are N synthetic UR5
simulators publishing namespaced telemetry, disclosed in
[`docs/context/VISION.md`](docs/context/VISION.md). The scaling proof is valid
because the broker and database see identical bytes either way.

## Non-negotiable conventions

- Type hints everywhere; `mypy --strict` passes.
- **Contracts-first:** every cross-service payload imports from `contracts/` —
  Pydantic v2 for MQTT/REST, protobuf for gRPC. A service that declares a
  payload shape locally is a bug, even if it works.
- **`robot_id` is a contract field, never a magic string.** It rides in the MQTT
  topic (`twin/<robot_id>/ur5/…`) *and* the payload envelope; services subscribe
  with `twin/+/ur5/#` and cross-check the two. No service hardcodes a robot, and
  nothing hardcodes N.
- **Additive-only schema evolution** (inherited ADR-0003 rule): `robot_id` and
  any new field are appended; protobuf field numbers are never reused; a
  `contracts/` CHANGELOG entry is mandatory and lands *before* the consumer.
- Protobuf stubs are generated into `contracts/gen/` and checked in; never
  hand-edit generated code.
- Every service has its own Dockerfile and exposes `/healthz` (liveness and
  readiness, distinctly) and `/metrics` (Prometheus format).
- `ruff` for lint and format; no `# noqa` without a justification comment.
- Tests colocated: `pytest` + `pytest-asyncio`; each service has at least one
  integration test.
- Conventional Commits: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.
- No new runtime dependency without a note in `docs/adr/`.

## Architecture rules

Follow the 5-layer stack in [`docs/context/ARCHITECTURE.md`](docs/context/ARCHITECTURE.md).
Service responsibilities are exclusive — do **not** cross them. The upstream
four keep their `twin-services` boundaries and only *learn to route by
`robot_id`*:

- `telemetry-svc` ingests and persists, tagging every point by `robot_id`. It
  does not compute derived state. One subscriber, wildcard topic — not one
  subscriber per robot.
- `state-svc` computes and serves state, one rolling window **per `robot_id`**.
  It does not persist anything.
- `command-svc` accepts and publishes commands for a given `robot_id`. It does
  not read state, and it does not know about the fleet — it moves one robot.
- `viz-svc` consumes `state-svc`'s gRPC stream only. The browser never touches
  MQTT or InfluxDB.
- **`fleet-svc` composes, it does not couple.** It discovers twins passively
  from the telemetry stream and fans a coordinated command out over
  `command-svc`'s REST. It is **not** on the telemetry-write path and **not** on
  the command-publish path. A fleet command is client-side composition across
  `command-svc`, not a new coupling — exactly the rule `twin-services` set.
- Grafana queries InfluxDB and Prometheus; it templates rows from the `robot_id`
  tag. It does not talk to MQTT or to services directly.

If a change would blur these boundaries — e.g. `fleet-svc` publishing setpoints
itself, or `command-svc` growing fleet awareness — propose an ADR instead of
writing the code.

## When you touch code

1. Read the relevant ADRs in `docs/adr/` — especially 0002 (namespacing),
   0003 (registry/discovery), 0004 (coordinated commands), and, once it exists,
   0005 (scaling breakpoint).
2. Schema changes land in `contracts/` first (CHANGELOG entry + regenerated
   stubs), then in services — never the reverse, never in the same commit as
   service logic.
3. Update tests in the same commit as the code. A fleet behaviour without a
   two-robot (or kill-one-of-N) test is not done.
4. If you add a public interface (MQTT topic, HTTP route, gRPC method, config
   key), document it in `docs/`.
5. Prefer editing existing files over creating new ones.
6. Keep functions under ~40 lines and modules under ~200 lines. Split by
   responsibility, not by file size.

## What to refuse

- **A sixth service.** `fleet-svc` is the only addition. Whatever the new idea
  is, it belongs in an existing service, in `fleet-svc`, or in a later repo.
- **Kubernetes, service mesh, multi-broker federation — as a default.** These
  are adopted only if ADR-0005's *measured* breakpoint demands them. "It looks
  production-grade" is not a reason; a latency number is.
- **Per-robot service instances as a starting assumption.** One `telemetry-svc`,
  one `state-svc`, one `command-svc` serve the whole fleet by routing on
  `robot_id`. Sharding by robot is a fix the load test might justify (ADR-0005),
  not a design premise.
- **Hardcoding N, or a specific `robot_id`, anywhere outside a test fixture.**
- **Real ROS 2 / Gazebo physics.** Synthetic simulators stand in; fidelity is
  `twin-cubesat`.
- **Cross-robot anomaly detection or ML.** Belongs in `twin-anomaly`.
- **AAS, OPC-UA, or any per-robot semantic modelling.** Belongs in `twin-aas`.
- **Message brokers other than Mosquitto, databases other than InfluxDB.**
- **Frontend scope growth in `viz-svc`.** One scene; if it shows the fleet,
  it shows them as instances of the same thin scene, no design system.

This repo demonstrates scale and coordination, not more architecture. Keep it
five services, and keep the breakpoint honest.
