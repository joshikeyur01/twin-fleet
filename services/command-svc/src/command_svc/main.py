"""Entrypoint: the publisher's connection loop and the API server as
sibling tasks.

Same crash policy as every service in this repo: if either task dies
unexpectedly the TaskGroup cancels the other and the process exits nonzero —
fail fast, let the container restart policy revive us (ADR-0004).
"""

from __future__ import annotations

import asyncio
import logging

import structlog
import uvicorn

from command_svc.api import build_app
from command_svc.config import CommandConfig
from command_svc.publisher import Publisher

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
    config = CommandConfig.from_env()
    publisher = Publisher(config)
    app = build_app(publisher)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.http_port, log_level="warning")
    )
    log.info("starting", http_port=config.http_port, mqtt=config.mqtt_host)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(publisher.run(), name="publisher")
        tg.create_task(server.serve(), name="api")


if __name__ == "__main__":
    asyncio.run(main())
