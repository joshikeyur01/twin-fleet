# 2. Robot namespacing: `robot_id` in the topic *and* the payload

Date: 2026-07-24
Status: Accepted

## Context

`twin-services` had one robot, so nothing on the wire carried a robot identity —
telemetry was `twin/ur5/joint/<name>/<field>`, commands were
`twin/ur5/cmd/joints`, and the payloads described only the joints. Generalising
to N robots forces exactly one new concept: an identity, `robot_id`.

The design question is *where that identity lives*. The wire touches three
places — the MQTT **topic**, the message **payload/envelope**, and (for gRPC)
the `state.proto` **message** — and the identity must be consistent across all
three. Candidates:

1. **Topic only** — `twin/<robot_id>/ur5/…`, payload unchanged.
2. **Payload only** — topic unchanged, `robot_id` field in the envelope.
3. **Both** — topic segment *and* envelope field, cross-checked.
4. **Registry-assigned opaque handle** — a broker-issued token instead of a
   human-readable `robot_1`.

This mirrors the ROS 2 question the spec points at: `/robot_1/joint_states` puts
the identity in the *namespace* (≈ the topic), while a `Header.frame_id` puts it
in the *message*. ROS 2, in practice, uses both.

## Decision

**`robot_id` lives in both the MQTT topic and the payload envelope**, and
consumers cross-check them.

- **Topic:** `twin/<robot_id>/ur5/joint/<name>/<field>` (telemetry),
  `twin/<robot_id>/ur5/cmd/joints` (commands). Services subscribe once with the
  wildcard `twin/+/ur5/#` and extract `robot_id` from the second segment via a
  `contracts` topic helper.
- **Envelope:** `robot_id` is an additive required field on `JointTelemetry`
  and `JointCommand` (`schema_version` bumped, `contracts/` CHANGELOG entry —
  per the inherited schema-evolution rule, `twin-services` ADR-0003).
- **gRPC:** `robot_id` is an additive field on `TwinState` and on the
  `GetState` request; field numbers appended, stubs regenerated.
- **Cross-check:** a consumer whose topic `robot_id` disagrees with the envelope
  `robot_id` raises a validation error. It is never silently accepted.
- **Format:** `robot_<n>` (`robot_1`, `robot_2`, …). Human-readable, sortable,
  and the canonical active set lives in one config, not scattered literals.

## Why both, and not one

The two locations answer two different questions:

- **The topic is how you *route*.** MQTT filters server-side, so one subscriber
  on `twin/+/ur5/#` receives all robots without N subscriptions, and a future
  shard can subscribe to `twin/robot_5/ur5/#` alone without touching payloads.
  Routing on a topic is what the broker is *for*; routing on a payload field
  means every consumer receives every robot's traffic and filters in-process —
  wasteful at 20 robots × 50 Hz, and it throws away the broker's one job.
- **The envelope is how you *verify* and *carry identity off the wire*.** An
  InfluxDB point is tagged by `robot_id` from the envelope without re-parsing the
  topic in every writer; a structured log line binds `robot_id` as a field; a
  replay file or a message pulled from a dead-letter path is still
  self-describing. A topic-only identity evaporates the moment the message
  leaves the broker.

Cross-checking the two is cheap and catches real bugs: a misrouted publish, a
simulator with a config typo, or a spoofed setpoint aimed at the wrong robot all
surface as a mismatch at the first consumer instead of as silent cross-talk.

## Consequences

Positive:
- One wildcard subscriber per service scales from 1 to N robots with no code
  change and no per-robot config — the property the whole repo depends on.
- InfluxDB series and Grafana rows key off the `robot_id` tag directly; the
  templated dashboard (Phase 4) is a straight consequence of this tag existing.
- Identity survives off the broker: logs, stored windows, replay, and the
  registry all read the same field.

Negative:
- Redundancy: `robot_id` is set in two places, so they *can* disagree. Accepted,
  and turned into a feature — the cross-check makes disagreement a loud failure,
  not a quiet corruption.
- A few extra bytes per envelope × 50 Hz × N. Measured in the load test, not
  hand-waved; if it ever matters it will show up in ADR-0005's throughput
  number.

## Alternatives considered

- **Topic only (1):** rejected. Fast to route, but the message is anonymous the
  instant it leaves MQTT — every consumer must re-parse the topic to tag or log,
  and any stored/replayed payload loses its identity. The persistence and
  registry paths both want the field.
- **Payload only (2):** rejected. The broker can no longer route or filter by
  robot; every service subscribes to one firehose and discards most of it, and
  the eventual "shard `telemetry-svc` by robot" option (a candidate ADR-0005 fix)
  becomes impossible without a topic to subscribe on. It also wastes the wildcard
  subscription that makes N-scaling trivial.
- **Registry-assigned opaque handle (4):** rejected as premature. `robot_1` is
  legible in a topic, a log, a curl, and a dashboard variable; an opaque token
  buys isolation we do not need at one trust domain and adds a lookup on the hot
  path. Revisit only if multi-tenant isolation ever enters scope — which
  [`VISION.md`](../context/VISION.md) explicitly rules out here.

## Dependency notes

No new runtime dependency. This decision is a contract shape and a topic
convention, both inside the existing `contracts` package; the only code cost is
a topic-parsing helper and its cross-check, plus regenerated gRPC stubs.
