"""state-svc: computes derived state (end-effector pose, velocity RMS) per
robot from MQTT telemetry; serves it over gRPC keyed by robot_id.

One rolling window per robot_id, created lazily on first sighting. GetState
selects a twin by robot_id; StreamState streams the whole fleet, each frame
tagged. Computes values, never persists — persistence is telemetry-svc's job."""
