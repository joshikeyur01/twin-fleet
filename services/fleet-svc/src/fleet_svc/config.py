"""Runtime configuration, loaded from environment variables.

Defaults match docker-compose.yml; localhost fallbacks exist so the service
can run outside a container against `just up` infra.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FleetConfig:
    mqtt_host: str
    mqtt_port: int
    command_svc_url: str
    http_port: int
    liveness_timeout_s: float
    fanout_concurrency: int

    @classmethod
    def from_env(cls) -> FleetConfig:
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            command_svc_url=os.getenv("COMMAND_SVC_URL", "http://localhost:8003"),
            http_port=int(os.getenv("HTTP_PORT", "8005")),
            # A robot unseen for this many seconds is dropped from the registry
            # (ADR-0003 liveness window; Last-Will is the fast path).
            liveness_timeout_s=float(os.getenv("FLEET_LIVENESS_TIMEOUT_S", "3.0")),
            # Bounded fan-out: at most this many concurrent command-svc calls
            # (ADR-0004 — a documented knob the load test can probe).
            fanout_concurrency=int(os.getenv("FLEET_FANOUT_CONCURRENCY", "8")),
        )
