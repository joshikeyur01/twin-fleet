# Roadmap

Six phases, two weeks total. Same rule as every `twin-*` repo: if a phase slips
more than two days, cut scope *inside* the phase — do not push the next phase.
The load-test ramp and its breakpoint ADR are the deliverable; everything else
is negotiable. This repo forks `twin-services`, so Phase 0 starts from working
code, not a blank directory.

## Phase 0 · Fork + scaffold (days 1–2)

- [ ] Copy the `twin-services` skeleton: `contracts/`, the four services,
      `deploy/`, `justfile`, `docker-compose.yml`, CI, pre-commit, `LICENSE`.
- [ ] Apply the known upstream fixes while forking (do **not** inherit the bugs):
      every Dockerfile's manifest layer copies each member `pyproject.toml` it
      depends on (the upstream miss); keep `.python-version` = 3.12; no
      `container_name` collisions. (Keep the `httpx2` dev dep — it is the current
      Starlette TestClient transport, not a typo for `httpx`.)
- [ ] Drop the twin-services `bridge`: no ROS 2 here, so the synthetic simulators
      publish namespaced MQTT directly (VISION's disclosed constraint).
- [ ] `pyproject.toml` `uv` workspace gains a `fleet-svc` member.
- [ ] `AGENTS.md`, `README.md`, `CHANGELOG.md` rewritten for the fleet mission.
- [ ] Baseline sanity: the forked stack still runs **one** robot end-to-end
      exactly as `twin-services` did.

**DoD:** `just up && just healthz` shows infra + five service stubs green on a
fresh clone (fleet-svc stub answers a hardcoded `/healthz`); the single-robot
telemetry path from `twin-services` still works unchanged.

## Phase 1 · Namespacing (days 3–4)

- [ ] `contracts/`: add required `robot_id` to `JointTelemetry` / `JointCommand`
      envelopes (additive, `schema_version` bumped, CHANGELOG entry); add
      `robot_id` to `state.proto` `TwinState` + `GetState` request, regenerate
      stubs into `contracts/gen/`.
- [ ] `telemetry-svc` subscribes `twin/+/ur5/#`, extracts `robot_id` from the
      topic, cross-checks the envelope, writes to InfluxDB tagged by `robot_id`.
- [ ] `command-svc` accepts `robot_id`, publishes to
      `twin/<robot_id>/ur5/cmd/joints`.
- [ ] ADR-0002 (robot namespacing: topic *and* payload) written **before** the
      second robot exists.
- [ ] Round-trip + compatibility tests: topic↔envelope `robot_id` mismatch
      fails validation; a pre-`robot_id` message is handled per the schema rule.

**DoD:** `just test` green; one robot still works, and telemetry is now
queryable by `robot_id` in InfluxDB even with a single twin.

## Phase 2 · N synthetic robots (days 5–7)

- [ ] Generalise the synthetic simulator into N independent instances, each
      parameterised by `robot_id`, publishing 50 Hz namespaced telemetry with a
      slight per-robot phase/seed offset (so twins are distinguishable, not
      clones).
- [ ] `state-svc`: one rolling window **per `robot_id`**; `GetState(robot_id)`
      returns one twin, `StreamState` streams all live twins tagged.
- [ ] `docker-compose` + `justfile`: `just fleet <k>` spins the fixed stack
      (infra + 5 services) then launches k individually-named simulator
      containers (`twin-fleet-robot-<i>`) on the compose network — a templated
      launcher, not Compose `--scale`, because the kill-one-robot demo needs
      per-container isolation and each robot needs a unique `robot_<n>` id.
- [ ] Integration test: publish two robots, assert two distinct InfluxDB series
      and two distinct `state-svc` results.

**DoD:** `just fleet 5` → five distinct joint traces in Grafana and five
distinct `GetState` responses; killing one simulator leaves the other four
unaffected.

## Phase 3 · fleet-svc: registry + coordination (days 8–10)

- [ ] `services/fleet-svc/`: passive discovery — subscribe `twin/+/ur5/#`,
      track last-seen per `robot_id`, mark dead on liveness-window miss + MQTT
      Last-Will; `GET /fleet` returns the live set with `robot_id`, last-seen,
      readiness.
- [ ] `contracts/`: `FleetCommand` / `FleetCommandResult` (per-robot result
      list); `fleet-svc` imports them — no local shape.
- [ ] `POST /fleet/command {"kind":"home"}` fans out one `command-svc` call per
      live robot, aggregates per-robot success/failure, returns the list.
- [ ] `/healthz` (liveness + readiness) and `/metrics` (fleet size, per-robot
      up gauge) on fleet-svc.
- [ ] ADR-0003 (registry by passive discovery) and ADR-0004 (best-effort
      coordinated commands) written with their accepted failure modes.
- [ ] Integration test: five robots, kill one → it leaves `GET /fleet` (5→4) and
      `POST /fleet/command` moves the four survivors; separately, a command-svc
      dispatch failure surfaces as a per-robot failed entry — never a fake 200.

**DoD:** `curl :8005/fleet` lists every live twin; killing a robot drops it from
that list within the liveness window; `curl -X POST :8005/fleet/command -d
'{"kind":"home"}'` moves all live robots, and with command-svc down every entry
in the response reports failed rather than a silent success.

## Phase 4 · Self-scaling dashboard (days 11–12)

- [ ] Grafana dashboard variable `robot_id` (query-driven from InfluxDB tags);
      one **templated row per robot** — joint traces + a per-robot up cell —
      generated by repeat, not hand-authored.
- [ ] Adding a robot adds a row with **zero** dashboard edits; removing one
      leaves a visible gap until it ages out.
- [ ] fleet-status panel: fleet size over time + per-robot readiness timeline.

**DoD:** `just fleet 8` then `just fleet 12` and the dashboard grows from 8 rows to 12
with no JSON edit; the provisioned dashboard is committed.

## Phase 5 · Load test + the breakpoint (days 13–14)

- [ ] `loadtest/`: ramp 1 → 20 robots (configurable), driving 50 Hz telemetry
      per robot and periodic coordinated commands; record **p50/p95/p99 command
      latency, MQTT broker CPU %, InfluxDB write throughput** at each ramp step.
- [ ] Emit a filled results table (CSV + a `docs/` markdown table) with the knee
      marked; ramp continues until a metric degrades, not to a fixed N.
- [ ] **ADR-0005 (scaling breakpoint)** — the headline deliverable: the metric
      that knees first, the N/Hz where, the cause, and the fix (or the reasoned,
      numbers-backed decision that single-broker Compose suffices and K8s stays
      out).
- [ ] Apply the fix ADR-0005 identifies (e.g. broker tuning, batched Influx
      writes, or sharding a service by `robot_id`) and re-run to show the knee
      moved — or record why it wasn't worth it.
- [ ] `just loadtest` reproduces the table; `WHAT_I_LEARNED.md` filled in.

**DoD:** Fresh clone → `just fleet 20 && just loadtest` reproduces the ramp
table with a marked breakpoint, and ADR-0005 explains it with the measured
number that justifies the conclusion. This is the GIF: robots climbing 1 → 20,
a latency curve bending, one line in an ADR naming why.

## Explicit non-goals for this repo

- Real ROS 2 / Gazebo multi-robot physics — synthetic simulators stand in;
  fidelity is `twin-cubesat`.
- Kubernetes / service mesh as a default — adopted only if ADR-0005's measured
  breakpoint demands it, never pre-emptively.
- A sixth application service — `fleet-svc` is the one addition; more
  coordination becomes an ADR, not a container.
- Cross-robot anomaly detection or ML — reuse `twin-anomaly`.
- Per-robot semantic / information models — `twin-aas`.
- mTLS, per-tenant auth, multi-broker federation — noted where they attach,
  not built. One trust domain, one broker, N robots.
