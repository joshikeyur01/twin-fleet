"""Entrypoint: one synthetic UR5, parameterised by env.

`just fleet N=` launches N of these as individually-named containers, each with a
distinct ROBOT_ID. ROBOT_ID is required — a simulator has no default identity.
"""

from __future__ import annotations

import asyncio
import logging
import os

import structlog

from simulators.publisher import Simulator

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
    robot_id = os.environ["ROBOT_ID"]  # required: no anonymous robots in a fleet
    simulator = Simulator(
        robot_id=robot_id,
        mqtt_host=os.getenv("MQTT_HOST", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        hz=float(os.getenv("TELEMETRY_HZ", "50")),
    )
    log.info("starting", robot_id=robot_id)
    await simulator.run()


if __name__ == "__main__":
    asyncio.run(main())
