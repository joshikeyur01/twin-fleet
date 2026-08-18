# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Repo scaffold, forked from `twin-services`: `contracts` + the four upstream
  services, extended to a fifth (`fleet-svc`) and N synthetic robots. The
  twin-services DDS↔MQTT `bridge` is dropped — with no ROS 2 on this machine,
  the synthetic simulators publish namespaced MQTT directly (see VISION).
- Docs-first context set: VISION, ARCHITECTURE, ROADMAP, STYLE, AGENTS.md — the
  fleet mission, the 5-layer stack with `fleet-svc`, and the disclosed
  synthetic-simulator constraint.
- **Namespacing:** `robot_id` added additively to the `contracts` envelopes and
  `state.proto` (`schema_version` bumped); services route on
  `twin/<robot_id>/ur5/…` via a `twin/+/ur5/#` wildcard and cross-check the
  envelope. InfluxDB points tagged by `robot_id`; `state-svc` keeps one rolling
  window per robot.
- `simulators/`: N independent synthetic UR5s, one process per `robot_id`,
  publishing 50 Hz namespaced telemetry with per-robot phase offsets.
- `services/fleet-svc/`: passive-discovery registry (`GET /fleet`), coordinated
  best-effort command fan-out (`POST /fleet/command`) with per-robot accounting,
  `/healthz` + `/metrics` (fleet size, per-robot up gauge).
- `contracts/`: `FleetCommand` / `FleetCommandResult` shapes, plus `FleetSnapshot`
  / `RobotStatus` for the `GET /fleet` registry response.
- `viz-svc`: fleet-aware — streams the whole fleet (empty robot_id filter) and
  renders one thin arm per `robot_id` on a grid, batching WS frames per animation
  frame so render cost tracks the display, not fleet size.
- `tests/integration/`: end-to-end stack tests (multi-robot, per-robot gRPC state,
  coordinated home, kill-one-of-N) — marked `slow`/`fleet`, self-skipping without
  the stack.
- Grafana dashboard templated by a `robot_id` variable — one repeated row per
  robot, plus a fleet-status timeline; scales with the fleet with no JSON edit.
- `loadtest/`: 1 → 20 ramp harness recording p50/p95/p99 command latency, MQTT
  broker CPU, and InfluxDB write throughput; emits the breakpoint table.
- Upstream fixes applied during the fork: each Dockerfile manifest layer copies
  every member `pyproject.toml` it depends on (the upstream miss); `.python-version`
  pinned to 3.12; no `container_name` collisions. (The `httpx2` dev dep is kept,
  not "fixed" to `httpx` — `httpx2` is the current Starlette 1.x TestClient
  transport and fleet-svc's HTTP client, not a typo.)
- **Batched InfluxDB writes** in `telemetry-svc` (flush 500 points or 0.5 s) —
  the ADR-0005 fix. Lifted ingest from a per-point ceiling of ~700 pts/s (hit at
  N=1) to **17,978 pts/s at N=20** (the full produced rate), ~25×.
- ADRs 0001–0005, all `Accepted`. **ADR-0005 (scaling breakpoint)** is filled
  from real ramps: the first ceiling was per-point Influx writes (not the broker
  — CPU ≤ 46 % at N=20); batching moved it 25×; single-broker Compose holds 20
  robots, so Kubernetes stays out with a measured reason.
- `WHAT_I_LEARNED.md` written.
- Quality: 95 unit tests + 5 live integration tests green, `mypy --strict` clean,
  `ruff` clean; all six images build (viz frontend renders 12 arms live). Verified
  end-to-end: `just fleet 5` → discovery in ~1 s → coordinated home 5/5 → kill a
  robot, dropped from the fleet in ~1 s.
