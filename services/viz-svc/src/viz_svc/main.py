"""Entrypoint: the channel watcher and the HTTP server as sibling tasks.

Same crash policy as every service in this repo: if either task dies
unexpectedly the TaskGroup cancels the other and the process exits nonzero —
fail fast, let the container restart policy revive us (ADR-0004).
"""

from __future__ import annotations

import asyncio
import logging

import structlog
import uvicorn

from viz_svc.app import build_app
from viz_svc.config import VizConfig
from viz_svc.stream import StateStream

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
    config = VizConfig.from_env()
    stream = StateStream(config)
    app = build_app(config, stream)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.http_port, log_level="warning")
    )
    log.info("starting", http_port=config.http_port, state_grpc=config.state_grpc_target)
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(stream.run(), name="channel-watch")
            tg.create_task(server.serve(), name="http")
    finally:
        await stream.close()


if __name__ == "__main__":
    asyncio.run(main())
