"""fleet-svc: the fleet registry and coordinated-command fan-out.

Discovers live twins passively from the telemetry stream it subscribes to
(`twin/+/ur5/#`) plus MQTT Last-Will for fast death detection — no registration
RPC (ADR-0003). Fans a coordinated command out across command-svc's REST with
per-robot accounting (ADR-0004): a dead twin is a failed entry, never a silent
drop. It composes; it never publishes setpoints itself and never touches storage
(AGENTS.md — off the telemetry-write and command-publish paths)."""
