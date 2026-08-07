# 4. Coordinated commands: best-effort fan-out with per-robot accounting

Date: 2026-07-24
Status: Accepted

## Context

`fleet-svc` exposes the first command that addresses many robots at once:
`POST /fleet/command {"kind":"home"}` should apply to every live twin. Single-
robot commands in `twin-services` had a clean synchronous contract (202 accepted
/ 422 invalid / 503 unavailable, "fail, don't buffer"). Fanning that out to N
robots forces a semantics question: **what does "all robots home" mean when some
robots fail?**

Candidates:

1. **Best-effort fan-out with per-robot accounting** — N independent commands,
   collect each outcome, return the list. Partial success is a normal, reported
   result.
2. **All-or-nothing (two-phase commit / saga)** — prepare all, commit all, or
   roll back so no robot moves unless every robot can.
3. **Fire-and-forget broadcast** — publish once to a shared command topic, no
   per-robot result.

## Decision

**Best-effort fan-out with per-robot accounting (1).** A fleet command is N
independent `command-svc` calls, one per **live** robot; `fleet-svc` aggregates
the outcomes; a dispatch that fails fails *its own* entry while the rest proceed.
It is explicitly **not** atomic.

- `fleet-svc` reads the live set from its registry (ADR-0003), then issues one
  typed command per live robot to `command-svc` — which publishes to
  `twin/<robot_id>/ur5/cmd/joints`. Fan-out is concurrent but **bounded** by a
  semaphore, so twenty robots do not open twenty uncontrolled connections; the
  bound is a documented knob the load test can probe.
- Results aggregate into a `contracts` `FleetCommandResult`: a list of
  `{robot_id, status, detail}`. The HTTP response is `200` if every dispatch
  succeeded and `207 Multi-Status` if the outcome is mixed — each robot's result
  is always explicit in the body.
- **What a failed entry means, honestly.** `command-svc` is fire-and-forget: it
  publishes a setpoint and returns 202 regardless of whether a robot is listening
  — it *cannot* observe a dead robot. So a failed entry is a genuine **dispatch**
  failure (command-svc unreachable, a 503 because the broker is down, a timeout),
  **never a fake 200**. A robot that has *died* does not appear as a failed entry;
  it has already left the registry, so `GET /fleet` shrinks and the fan-out simply
  targets the survivors. Two honest degradation modes, not one: kill a **robot**
  and the fleet shrinks; take **command-svc or the broker** down and every
  dispatch comes back failed.
- `fleet-svc` composes over `command-svc`'s REST; it does **not** publish
  setpoints itself (per `AGENTS.md`). Each robot's outcome is exactly
  `command-svc`'s existing 202/503, just collected.

## Why best-effort, not atomic

**There is no honest rollback for actuation.** A "home" that reached 15 of 20
robots cannot be un-sent; "rolling back" would mean commanding those 15 back to
their prior pose — itself a fallible command that can partially fail, so the
atomicity was never real. Two-phase commit borrows a database guarantee that
physical (or physics-simulated) actuators cannot honour.

Robots are independent actuators, not rows in one table. "18 homed, 2 were dead"
is a *legitimate operational state*, and the honest API returns it rather than
collapsing it into a single 200 or 500. This is the same philosophy as
`twin-services`' single-robot command path — a synchronous, honest status —
preserved per robot instead of abandoned at the fleet boundary.

Commands here are idempotent (`home` is idempotent; setpoints are idempotent per
`STYLE.md`), so a caller can safely retry just the failed subset without any
coordinator — which is the cheap, correct alternative to a distributed
transaction.

## Consequences

Positive:
- The degradation demos have teeth. Kill a **robot**: it leaves the registry,
  `GET /fleet` drops from N to N−1, and the next coordinated command moves the
  survivors. Take **command-svc or the broker** down: every dispatch comes back a
  failed entry, never a fake 200. That visible per-robot accounting *is* the
  deliverable value of coordinated control.
- No coordinator, no prepare state, no rollback path to test — the failure
  surface is N independent, already-tested single-robot calls.
- Retry is trivial and safe: re-issue to the failed subset; idempotency makes it
  a no-op on any that actually succeeded.

Negative:
- **Not simultaneous.** "All home" is N commands over a bounded pool, not a
  synchronised millisecond-aligned trigger. For independent arms this is fine; a
  use case needing true simultaneity would require time-stamped setpoints,
  explicitly out of scope.
- **Caller owns partial-failure policy.** `fleet-svc` reports; it does not decide
  whether 18/20 is success. That judgement belongs to the caller, and the 207
  body gives them what they need to make it.
- Fan-out concurrency is a new pressure point on `command-svc` and the broker —
  intentionally, so the load test can find its ceiling (ADR-0005).

## Alternatives considered

- **Two-phase commit / saga (2):** rejected. Atomicity across physical actuators
  is unachievable (no true rollback), and a coordinator adds prepare state and
  failure modes that dwarf the benefit. The thesis argument is decomposition and
  scale, not distributed transactions.
- **Fire-and-forget broadcast (3):** rejected. Publishing once to a shared
  command topic throws away the per-robot accounting that is the entire point,
  reintroduces payload-side routing that ADR-0002 rejected (each simulator would
  filter a shared topic), and bypasses `command-svc`'s per-robot validation.
- **`fleet-svc` publishing setpoints directly:** rejected. It violates the
  `AGENTS.md` boundary — `command-svc` owns the publish path. `fleet-svc`
  composes, it does not couple.

## Dependency notes

- `httpx2` (async) in `fleet-svc` — the client that calls `command-svc`'s REST
  during fan-out. It is `httpx2`, the next-generation successor to `httpx`, and
  the same package Starlette 1.x's TestClient now requires; using it for both the
  runtime client and the test transport keeps one HTTP library in the workspace.
- No other new runtime dependency: the registry side is `aiomqtt` (ADR-0003) and
  the contract shapes live in `contracts`.
