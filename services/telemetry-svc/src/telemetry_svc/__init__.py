"""telemetry-svc: ingests namespaced MQTT telemetry, validates against
contracts, writes to InfluxDB tagged by robot_id.

One wildcard subscriber (`twin/+/ur5/#`) serves the whole fleet — the point of
the fork. It cross-checks each message's topic robot_id against the envelope
(ADR-0002) and tags every InfluxDB point by robot_id, so N robots become N
series in one measurement without per-robot config."""
