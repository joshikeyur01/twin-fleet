# 1. Record architecture decisions

Date: 2026-07-24
Status: Accepted

## Context

`twin-fleet` forks `twin-services` and generalises it from one robot to N. That
fork inherits four accepted decisions and adds several of its own —
namespacing, a registry model, coordinated-command semantics, and eventually the
scaling breakpoint. We need the same lightweight, recoverable record of the
non-obvious ones, so that six months from now — or when `twin-cubesat` forks
this repo — the reasoning survives.

## Decision

We will use Architecture Decision Records (ADRs) as described by Michael Nygard.
Each ADR is a short markdown file in `docs/adr/`, numbered in sequence. Format:
Context → Decision → Consequences.

Status is one of: Proposed, Accepted, Deprecated, Superseded by ADR-XXXX.

ADR numbering is **per-repo**: this sequence starts fresh at 0001, exactly as
`twin-services` and `twin-anomaly` did. Where a `twin-fleet` decision inherits a
`twin-services` rule unchanged (e.g. schema evolution), the ADR references the
upstream number rather than restating it.

## Consequences

- Every meaningful design decision is discoverable via `ls docs/adr/`.
- PRs that introduce a new dependency or cross a layer boundary must include an
  ADR (or a dependency note inside an existing one).
- ADRs are append-only. Corrections happen via a new ADR that supersedes.
- ADR-0005 (the scaling breakpoint) is written **after** its evidence exists —
  a deliberate exception to writing the decision before the code, because its
  entire content is a measured result. It ships as a Proposed placeholder until
  the load test fills it.
