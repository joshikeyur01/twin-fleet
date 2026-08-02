"""Entrypoint: the ingest loop and the health server as sibling tasks.

If either task dies unexpectedly the TaskGroup cancels the other and the
process exits nonzero — that is the crash policy: fail fast, let the
container restart policy revive us (ADR-0004).
"""

from __future__ import annotations

import asyncio
import logging

import structlog
import uvicorn

from telemetry_svc.config import TelemetryConfig
from telemetry_svc.health import build_health_app
from telemetry_svc.ingest import Ingestor

log = structlog.get_logger()


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


async def main() -> None:
    configure_logging()
    config = TelemetryConfig.from_env()
    ingestor = Ingestor(config)
    app = build_health_app("telemetry-svc", ingestor.readiness)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.http_port, log_level="warning")
    )
    log.info("starting", http_port=config.http_port, mqtt=config.mqtt_host)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(ingestor.run(), name="ingest")
        tg.create_task(server.serve(), name="health")


if __name__ == "__main__":
    asyncio.run(main())
