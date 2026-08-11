"""viz-svc: serves the React + react-three-fiber viewer and proxies state-svc's
StreamState gRPC into a WebSocket.

Fleet-aware only at the seam: it streams the whole fleet (empty robot_id filter)
and tags each browser frame with robot_id, so the viewer can render one thin arm
per robot. It stays one scene of instances — no per-robot controls, no design
system (AGENTS.md). The fleet dashboard that matters is Grafana (ROADMAP Phase 4)."""
