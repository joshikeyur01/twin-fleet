"""Entrypoint: consumer, gRPC server, and health server as sibling tasks.

Same crash policy as every service in this repo: if any task dies unexpectedly
the TaskGroup cancels the rest and the process exits nonzero — fail fast, let
the container restart policy revive us (ADR-0004).
"""

from __future__ import annotations

import asyncio
import logging

import grpc
import structlog
import uvicorn

from state_svc.config import StateConfig
from state_svc.consumer import Consumer
from state_svc.grpc_server import StateHub, build_server
from state_svc.health import build_health_app

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


async def serve_grpc(server: grpc.aio.Server) -> None:
    await server.start()
    await server.wait_for_termination()


async def main() -> None:
    configure_logging()
    config = StateConfig.from_env()
    hub = StateHub()
    consumer = Consumer(config, hub)
    health_app = build_health_app("state-svc", consumer.readiness)
    http_server = uvicorn.Server(
        uvicorn.Config(health_app, host="0.0.0.0", port=config.http_port, log_level="warning")
    )
    grpc_server = build_server(hub, config.grpc_port)
    log.info(
        "starting",
        http_port=config.http_port,
        grpc_port=config.grpc_port,
        mqtt=config.mqtt_host,
    )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(consumer.run(), name="consumer")
        tg.create_task(serve_grpc(grpc_server), name="grpc")
        tg.create_task(http_server.serve(), name="health")


if __name__ == "__main__":
    asyncio.run(main())
