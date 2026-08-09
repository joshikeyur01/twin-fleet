"""Entrypoint: the registry's discovery loop and the API server as sibling tasks.

Same crash policy as every service in this repo: if either task dies unexpectedly
the TaskGroup cancels the other and the process exits nonzero — fail fast, let the
container restart policy revive us (ADR-0004).
"""

from __future__ import annotations

import asyncio
import logging

import structlog
import uvicorn

from fleet_svc.api import build_app
from fleet_svc.config import FleetConfig
from fleet_svc.fanout import FanOut
from fleet_svc.registry import Registry

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
    config = FleetConfig.from_env()
    registry = Registry(config)
    fanout = FanOut(config)
    app = build_app(registry, fanout)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.http_port, log_level="warning")
    )
    log.info(
        "starting",
        http_port=config.http_port,
        command_svc=config.command_svc_url,
        mqtt=config.mqtt_host,
    )
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(registry.run(), name="registry")
            tg.create_task(server.serve(), name="api")
    finally:
        await fanout.close()


if __name__ == "__main__":
    asyncio.run(main())
