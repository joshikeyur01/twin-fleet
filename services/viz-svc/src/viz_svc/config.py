"""Runtime configuration, loaded from environment variables.

Defaults match docker-compose.yml; localhost fallbacks exist so the service
can run outside a container against `just up` infra.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VizConfig:
    state_grpc_target: str
    http_port: int
    static_dir: str

    @classmethod
    def from_env(cls) -> VizConfig:
        return cls(
            state_grpc_target=os.getenv("STATE_GRPC_TARGET", "localhost:50051"),
            http_port=int(os.getenv("HTTP_PORT", "8004")),
            static_dir=os.getenv("STATIC_DIR", "frontend/dist"),
        )
