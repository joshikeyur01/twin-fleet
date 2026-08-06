"""Runtime configuration, loaded from environment variables.

Defaults match docker-compose.yml; localhost fallbacks exist so the service
can run outside a container against `just up` infra.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandConfig:
    mqtt_host: str
    mqtt_port: int
    http_port: int

    @classmethod
    def from_env(cls) -> CommandConfig:
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            http_port=int(os.getenv("HTTP_PORT", "8003")),
        )
