# Style

Inherits from the `twin-*` portfolio-wide conventions in
[`twin-arch`](https://github.com/joshikeyur01/twin-arch/blob/main/docs/style.md),
from `twin-hello`, and from `twin-services` (this repo's fork parent). Only
deltas and fleet specifics are documented here.

## Workspace

- One uv workspace, seven members: `contracts`, the four upstream services,
  `fleet-svc`, and `simulators`. There is no `bridge` — with no ROS 2 on this
  machine the simulators publish namespaced MQTT directly, so the twin-services
  DDS↔MQTT bridge has no role here. The root `pyproject.toml` owns tool config
  and dev deps; members own their runtime deps. `.python-version` pins 3.12 — do
  not rely on `requires-python` alone (uv will happily pick 3.14).
- A service's dependency list is an architecture statement: only telemetry-svc
  may depend on the Influx client, only state-svc on numpy, viz-svc must have no
  MQTT dependency, and **fleet-svc must have no Influx dependency** — it reads
  the fleet from MQTT and acts through `command-svc`'s REST, nothing else.

## Namespacing (the fleet delta)

- `robot_id` is a `contracts` field, never a hand-built string. Format:
  `robot_<n>` (`robot_1`, `robot_2`, …); the canonical set lives in one config,
  not scattered literals.
- It appears in **both** the MQTT topic and the payload envelope. Producers set
  both; consumers subscribe with the `twin/+/ur5/#` wildcard, extract `robot_id`
  from the topic segment via a `contracts` helper, and cross-check against the
  envelope. A mismatch raises — it is never silently accepted.
- Nothing hardcodes N. A service handling one robot and a service handling
  twenty differ only in how many `robot_id`s flow through the same code path.

## Contracts

- Every payload crossing MQTT, REST, or gRPC imports from `contracts/`. A
  `BaseModel` subclass inside `services/` fails CI.
- Topic strings — now including the `robot_id` segment — are built by
  `contracts` helpers, never by hand.
- Evolution rules are the inherited ADR-0003 and they are not suggestions:
  `robot_id` was added additively, `schema_version` bumped, CHANGELOG first.

## Python

- Target 3.12, `mypy --strict`, ruff with the shared select list.
- Pydantic v2 for JSON contracts; frozen slotted dataclasses for config.
- Async for I/O; each service is one process, one `asyncio.TaskGroup`,
  fail-fast. Expected failures are handled inside tasks.
- Per-robot state lives in a keyed structure (`dict[str, Window]`), created
  lazily on first sighting of a `robot_id` and evicted on liveness timeout —
  never a fixed-size array indexed by robot number.
- Structured logging via structlog, JSON renderer, event names like
  `robot_seen` / `fleet_command_fanned_out` — greppable, and `robot_id` is a
  bound log field, not interpolated into the message.

## gRPC

- Proto packages are versioned (`twin.state.v1`); breaking changes mean a new
  package alongside, never mutation. `robot_id` was an additive field, not a
  break.
- Streams drop, never buffer. `StreamState` streams all live twins; a lagging
  client is the client's problem.

## MQTT

- Topic scheme: `twin/<robot_id>/ur5/joint/<joint>/<field>` for telemetry,
  `twin/<robot_id>/ur5/cmd/joints` for commands. The `<robot_id>` segment is the
  only structural change from `twin-services`.
- QoS 0 for telemetry (a lost sample is noise), QoS 1 for commands (setpoints
  are idempotent, so at-least-once is safe).
- fleet-svc uses MQTT Last-Will per simulator for fast death detection; the
  liveness timeout is the slow-path backstop.

## Metrics and health

- Prometheus metric names start `twin_`; every service exports
  `twin_service_ready{service=...}`. Per-robot series carry a `robot_id` label
  (e.g. `twin_fleet_robot_up{robot_id=...}`) — but keep cardinality bounded to
  the active fleet; do not label high-frequency histograms by `robot_id` unless
  the load test needs it.
- fleet-svc exports `twin_fleet_size` and `twin_fleet_robot_up{robot_id=...}`.
- `/healthz/live` and `/healthz/ready` on every service, same JSON shape.
  Readiness lists every dependency by name.

## Simulators

- One process per `robot_id`, parameterised by CLI/env; identical code, distinct
  `robot_id` and a per-robot phase/seed offset so twins are distinguishable, not
  byte-clones. A simulator publishes and exposes nothing else — no port.

## Load test

- `loadtest/` records p50/p95/p99 from raw samples (compute percentiles from the
  full latency array, never from pre-bucketed averages). Every run stamps the
  ramp step, N, and Hz alongside the numbers so a table row is self-describing.
- Broker CPU and InfluxDB throughput are read from the same source the
  dashboard uses, not a second measurement path — one source of truth for the
  breakpoint number that ADR-0005 will cite.

## Tests

- pytest + pytest-asyncio auto mode. Unit tests import no broker, no database,
  no ROS. `slow` marks stack-dependent integration tests; `chaos` marks
  container-killing tests.
- **A fleet behaviour needs a multi-robot test.** The canonical one: five
  robots, kill one, assert the coordinated command moves four and reports the
  fifth as failed — not silently dropped.
- Literal status codes and values in test asserts are fine (per-file PLR2004
  ignore) — that is what tests are for.

## Commits and branches

- Conventional Commits. Scope is the member or service:
  `feat(fleet-svc): ...`, `fix(contracts): ...`, `chore(loadtest): ...`.
- Schema changes: `contracts` commit first, adopters after (inherited ADR-0003).
- Trunk-based; squash merges; tag `v0.x.y`.
