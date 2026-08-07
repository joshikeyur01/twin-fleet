# 3. Fleet registry by passive discovery

Date: 2026-07-24
Status: Accepted

## Context

`fleet-svc` must know which twins are alive — to answer `GET /fleet` and to fan
a coordinated command out only to live robots. `twin-services` deferred exactly
this: "real discovery pressure arrives with N robots in `twin-fleet`." Here it
is.

How does `fleet-svc` learn the live set? Candidates:

1. **Explicit registration** — each simulator `POST`s a register/heartbeat to
   `fleet-svc` and deregisters on shutdown.
2. **Passive discovery** — `fleet-svc` subscribes to the telemetry stream it can
   already see (`twin/+/ur5/#`), records last-seen per `robot_id`, and marks a
   robot dead when it misses a liveness window.
3. **MQTT Last-Will + birth** — simulators set an MQTT Last-Will the broker
   publishes on ungraceful disconnect, and optionally a retained birth message.
4. **Broker introspection** — query Mosquitto (`$SYS`, client list) for
   connected clients.

## Decision

**Passive discovery (2) is the registry, with MQTT Last-Will (3) as the
fast-path death signal.** There is no registration RPC.

- `fleet-svc` subscribes to `twin/+/ur5/#`, extracts `robot_id` (via the
  ADR-0002 topic helper), and updates a `dict[str, datetime]` of last-seen
  times. First sighting of a new `robot_id` adds it to the fleet.
- A robot is **alive** if last-seen is within the liveness window
  (a documented multiple of the telemetry publish interval, not a magic
  constant), else **dead** and dropped from `GET /fleet`.
- Each simulator sets an MQTT **Last-Will** on a presence topic
  (`twin/<robot_id>/ur5/status`); the broker publishes it on ungraceful
  disconnect, letting `fleet-svc` mark the robot dead immediately instead of
  waiting out the window. The liveness window is the slow-path backstop for
  deaths the will does not cover (e.g. a wedged-but-connected simulator).
- `GET /fleet` returns each live `robot_id` with its last-seen timestamp and a
  derived readiness. No robot ever talks *to* `fleet-svc`.

## Why passive, not registered

**"Alive" should mean "producing valid telemetry," and passive discovery makes
that the literal definition.** A robot that registered but stopped publishing is
functionally dead; an explicit registry would still list it until a heartbeat
times out — so registration needs a heartbeat anyway, at which point it is just
passive discovery with a second, redundant channel that can disagree with the
telemetry ground truth.

Deriving the registry from the stream that already flows means:

- **One source of truth.** The fleet is exactly the set of robots whose data is
  arriving. There is no way for the registry to claim a robot is up while its
  telemetry is absent.
- **Dumb simulators.** They publish and expose nothing (per `STYLE.md`) — no
  client of `fleet-svc`, no registration credential, no shutdown hook that must
  fire for correctness. This matters at 20 robots and in the load test, where
  processes are killed abruptly on purpose.
- **The seam that `twin-cubesat` reuses.** A servicer discovering a target by
  the telemetry it emits is closer to the space-domain reality than a target
  that politely registers itself.

## Consequences

Positive:
- `fleet-svc` depends only on `aiomqtt` for discovery — no registration store,
  and (per `AGENTS.md`) no Influx dependency. It reads the fleet from MQTT and
  acts through `command-svc`.
- Killing a simulator → Last-Will fires (or the window expires) → it drops from
  `GET /fleet` → the Grafana fleet-status timeline shows the gap. This *is* the
  kill demo, inherited from `twin-services` and now fleet-scaled.
- New robots need zero coordination: start a simulator, its first publish
  enrolls it.

Negative:
- **Discovery latency on birth:** a robot appears only after its first telemetry
  message — sub-20 ms at 50 Hz, negligible.
- **Liveness-window tuning:** too short flaps on a single dropped sample; too
  long lets a dead robot linger. Set as a multiple of the publish interval and
  documented; the load test will show whether it holds under broker stress
  (candidate input to ADR-0005).
- **Readiness is coarse** — "producing telemetry" only. A robot that is up but
  self-reporting degraded would need a richer presence payload on the status
  topic. Noted as a future extension, out of scope here.

## Alternatives considered

- **Explicit registration (1):** rejected. Adds a second source of truth that
  can disagree with the telemetry, still needs a heartbeat to detect silent
  death, and makes simulators into `fleet-svc` clients that must deregister
  cleanly — the opposite of what abrupt kills in the load test need.
- **Broker introspection via `$SYS` / client list (4):** rejected. Mosquitto-
  specific and brittle; a connected TCP client is not the same as a robot
  emitting valid telemetry; and it couples `fleet-svc` to broker internals.
  (`twin-hello` already drew blood on a `$SYS` health-check bug — reason enough
  to keep `$SYS` off the critical path.)
- **Retained birth message as the registry (3 alone):** rejected as the primary.
  Retained presence can go stale if a simulator dies in a way that does not
  cleanly trigger will delivery; telemetry-derived last-seen is the ground
  truth. Last-Will is kept, but as an accelerator on top of passive discovery,
  not the registry itself.

## Dependency notes

No new runtime dependency: `aiomqtt` is already in the stack. `fleet-svc`'s
registry is a subscriber plus a keyed last-seen map plus a liveness comparison —
no store, no new library.
