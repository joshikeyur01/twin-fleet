# Vision

## Why this repo exists

`twin-services` proved the decomposition — four services, versioned contracts,
graceful partial failure — for exactly **one** robot. That was the honest limit
of what a single UR5 could teach. But the thesis argues *about scale*: a
service-oriented twin is only interesting because you can run a fleet of them,
and every claim about namespacing, service discovery, coordinated control, and
where the architecture buckles is a claim I can't make credibly until I've
watched it buckle.

This repo takes the `twin-services` stack and generalises it from one robot to
**N**. The changes are small in count and large in consequence:

- **Namespacing.** Every robot gets an identity. Telemetry, commands, and state
  are keyed by `robot_id`, mirroring ROS 2 namespacing (`/robot_1/joint_states`)
  at the MQTT topic layer (`twin/robot_1/ur5/…`). The existing four services
  become fleet-aware without changing their contracts' *shape* — only their
  *cardinality*.
- **A registry.** `fleet-svc` tracks which twins are alive, answers
  `GET /fleet`, and issues coordinated commands ("all robots home") — the first
  time a command fans out to many recipients instead of one.
- **A dashboard that scales itself.** Grafana rows are templated per robot, so
  the view grows with the fleet instead of being hand-edited per robot.
- **A load test that is the point, not an afterthought.** `loadtest/` ramps
  1 → 20 robots and records p50/p95/p99 command latency, MQTT broker CPU, and
  InfluxDB write throughput — until something gives.

The one-sentence version: **`twin-services` was an architecture for one robot;
this is a fleet, and the deliverable is finding where it breaks.**

## The honest constraint (read this before the demo)

There is no ROS 2 or Gazebo on this machine, exactly as in `twin-hello`,
`twin-services`, and `twin-anomaly`. "N robots" therefore means **N synthetic
simulators**, each publishing UR5-shaped telemetry to its own namespaced MQTT
topic tree. This is a deliberate, disclosed substitution, and it costs the demo
nothing that matters here:

- The **namespacing discipline** is real — every service must route by
  `robot_id` or it breaks with two robots, let alone twenty.
- The **coordination semantics** are real — "all robots home" fans out to N
  independent twins and must degrade gracefully if one is dead.
- The **load ceiling** is real — a broker, a database, and a fan-out service do
  not care whether the 50 Hz joint stream came from Gazebo physics or from a
  numpy sine wave. The bytes, the topic count, and the write volume are
  identical.

What is *not* exercised: real multi-robot physics, collision, or contact. If the
thesis needs those, they live in `twin-cubesat`'s Gazebo world, not here. This
repo is an infrastructure-scaling proof, and it is honest about being one.

## What "done" looks like

- `just fleet 20` (or any N) starts one broker, InfluxDB, Grafana,
  Prometheus, the four `twin-services` services, `fleet-svc`, and N synthetic
  robot simulators — each on its own namespace.
- `GET /fleet` lists every live twin with its `robot_id`, last-seen timestamp,
  and readiness; killing a simulator drops it from the registry within one
  liveness interval and the dashboard shows the gap.
- A single coordinated command — `POST /fleet/command {"kind":"home"}` — moves
  **all** live robots, and reports per-robot success/failure so a dead twin is
  visible in the response, not silently dropped.
- The Grafana dashboard renders one row per robot **automatically** from a
  template; adding a robot adds a row with no dashboard edit.
- `just loadtest` produces a filled table — p50/p95/p99 command latency, broker
  CPU %, InfluxDB write throughput — across the 1 → 20 ramp, with the
  breakpoint marked.
- **The one artefact that must exist:** an ADR that names the observed scaling
  breakpoint, explains *why* it is where it is, and documents the fix (or the
  reasoned decision not to fix it, with the number that justifies waiting).
  `twin-services` promised "twin-fleet's load test will find the ceiling on
  purpose" — this ADR is that promise paid.

## What "done" does not look like

- **Real ROS 2 / Gazebo multi-robot.** Synthetic simulators stand in, disclosed
  above. Physics fidelity belongs in `twin-cubesat`.
- **Kubernetes or a service mesh as a foregone conclusion.** `twin-services`
  called this repo their "earliest honest home — and maybe not even there." The
  load test decides: if Compose-scaled Mosquitto holds 20 robots, the ADR says
  so and orchestration stays out. We adopt K8s only if a *measured* ceiling
  demands it, never because it looks serious on a diagram.
- **A sixth application service.** `fleet-svc` is the one addition. Coordination
  logic that wants to grow becomes an ADR, not a new container.
- **Cross-robot anomaly detection or ML.** That reuse belongs in `twin-anomaly`.
- **Semantic / information models per robot.** That's `twin-aas`.
- **Real multi-tenant auth or per-robot isolation.** Noted where mTLS and
  per-tenant credentials would attach; not implemented. One trust domain,
  localhost, N robots.

Generalising 1 → N is already the maximum honest scope. If a feature doesn't
help route by `robot_id`, coordinate across the fleet, or find the ceiling, it
is scope creep wearing a fleet diagram.

## Audience

Same three people as every `twin-*` repo, in order:

1. **Me, forking this for `twin-cubesat`**, where one servicer coordinates with
   one target and the fleet primitives (namespacing, coordinated state machine
   steps) reappear in the space domain. The seams must survive the fork.
2. **A thesis examiner** who wants the word "scale" in the text to map to a
   measured breakpoint with a number and a cause, not an assertion that the
   system "scales well."
3. **A recruiter or PI** who watches the 1 → 20 ramp GIF and the "all robots
   home" coordinated command, and understands fleet control and honest capacity
   planning in fifteen seconds.

If a change doesn't help at least one of those three, it doesn't ship.
