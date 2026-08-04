"""Runtime configuration, loaded from environment variables.

Defaults match docker-compose.yml; localhost fallbacks exist so the service
can run outside a container against `just up` infra.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StateConfig:
    mqtt_host: str
    mqtt_port: int
    http_port: int
    grpc_port: int
    rms_window_s: float
    state_rate_hz: float

    @classmethod
    def from_env(cls) -> StateConfig:
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            http_port=int(os.getenv("HTTP_PORT", "8002")),
            grpc_port=int(os.getenv("GRPC_PORT", "50051")),
            rms_window_s=float(os.getenv("RMS_WINDOW_S", "2.0")),
            state_rate_hz=float(os.getenv("STATE_RATE_HZ", "50")),
        )
