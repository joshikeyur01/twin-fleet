"""Coordinated-command fan-out (ADR-0004).

Turns one FleetCommand into one JointCommand per live robot and issues them to
command-svc concurrently, bounded by a semaphore. Best-effort with per-robot
accounting: each entry's status is the *dispatch* outcome (202 accepted → OK;
unreachable / 503 / timeout → FAILED with a reason). command-svc is
fire-and-forget, so a failed entry is a genuine dispatch failure, never a fake
200 — and never a rollback. Idempotent commands make retrying the failed subset
safe (that is the cheap alternative to a distributed transaction).
"""

from __future__ import annotations

import asyncio

import httpx2
import structlog
from prometheus_client import Counter, Histogram

from contracts import (
    FleetCommand,
    FleetCommandResult,
    FleetCommandStatus,
    JointCommand,
    RobotCommandResult,
)
from fleet_svc.config import FleetConfig

log = structlog.get_logger()

FANOUTS = Counter("twin_fleet_fanouts_total", "Coordinated commands fanned out.", ["kind"])
ROBOT_RESULTS = Counter("twin_fleet_dispatch_total", "Per-robot dispatch outcomes.", ["status"])
LATENCY = Histogram(
    "twin_fleet_command_latency_seconds",
    "End-to-end coordinated command latency (whole fan-out).",
)

HTTP_ACCEPTED = 202  # command-svc's success code: setpoint accepted for publish


class FanOut:
    """Fans a FleetCommand out over command-svc's REST with bounded concurrency."""

    def __init__(self, config: FleetConfig) -> None:
        self._config = config
        self._client = httpx2.AsyncClient(base_url=config.command_svc_url, timeout=5.0)
        self._sem = asyncio.Semaphore(config.fanout_concurrency)

    async def close(self) -> None:
        await self._client.aclose()

    async def dispatch(self, command: FleetCommand, robots: list[str]) -> FleetCommandResult:
        """Issue the command to every robot; aggregate per-robot outcomes."""
        FANOUTS.labels(kind=command.kind.value).inc()
        with LATENCY.time():
            results = await asyncio.gather(*(self._dispatch_one(command, r) for r in robots))
        for result in results:
            ROBOT_RESULTS.labels(status=result.status.value).inc()
        return FleetCommandResult(kind=command.kind, results=list(results))

    async def _dispatch_one(self, command: FleetCommand, robot_id: str) -> RobotCommandResult:
        body = JointCommand(
            robot_id=robot_id,
            kind=command.kind,
            positions=command.positions,
            duration_s=command.duration_s,
        )
        async with self._sem:
            try:
                resp = await self._client.post("/command", json=body.model_dump(mode="json"))
            except httpx2.HTTPError as exc:
                return RobotCommandResult(
                    robot_id=robot_id, status=FleetCommandStatus.FAILED, detail=str(exc)
                )
        if resp.status_code == HTTP_ACCEPTED:
            command_id = resp.json().get("command_id")
            return RobotCommandResult(
                robot_id=robot_id, status=FleetCommandStatus.OK, command_id=command_id
            )
        return RobotCommandResult(
            robot_id=robot_id,
            status=FleetCommandStatus.FAILED,
            detail=f"command-svc returned {resp.status_code}",
        )
