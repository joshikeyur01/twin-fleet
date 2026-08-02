"""Runtime configuration, loaded from environment variables.

Defaults match docker-compose.yml; localhost fallbacks exist so the service
can run outside a container against `just up` infra.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    mqtt_host: str
    mqtt_port: int
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    http_port: int

    @classmethod
    def from_env(cls) -> TelemetryConfig:
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            influx_url=os.getenv("INFLUX_URL", "http://localhost:8086"),
            influx_token=os.getenv("INFLUX_TOKEN", "dev-token-change-me"),
            influx_org=os.getenv("INFLUX_ORG", "twin"),
            influx_bucket=os.getenv("INFLUX_BUCKET", "telemetry"),
            http_port=int(os.getenv("HTTP_PORT", "8001")),
        )
