"""The REST face of command-svc: POST /command plus the standard health
endpoints, one app.

The request body is contracts.JointCommand (which carries robot_id) and the
response is contracts.CommandReceipt — this layer adds transport concerns (IDs,
status codes), never payload shapes. Malformed commands (unknown kind, bad
robot_id, move_joints without positions) are rejected by the contract itself:
FastAPI surfaces Pydantic validation as 422 before any handler runs.

This service is already an HTTP app, so health lives here rather than in a
separate health.py; the endpoints match the other services exactly (ADR-0004).
"""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import FastAPI, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Gauge, generate_latest

from command_svc.publisher import BrokerUnavailableError, Publisher
from contracts import CommandReceipt, JointCommand


def _service_ready_gauge() -> Gauge:
    """In production each service is its own process; only the test suite
    imports several services at once, colliding on this shared gauge name."""
    try:
        return Gauge(
            "twin_service_ready",
            "1 when every dependency check passes, else 0.",
            ["service"],
        )
    except ValueError:
        return cast(Gauge, REGISTRY._names_to_collectors["twin_service_ready"])


_READY = _service_ready_gauge()


def build_app(publisher: Publisher) -> FastAPI:
    app = FastAPI(title="command-svc")
    ready_gauge = _READY.labels(service="command-svc")

    @app.post("/command", status_code=status.HTTP_202_ACCEPTED)
    async def command(body: JointCommand) -> CommandReceipt:
        """Accept a setpoint for one robot for asynchronous execution (202, not
        200: the receipt confirms publication, not completed robot motion)."""
        try:
            topic = await publisher.publish(body)
        except BrokerUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return CommandReceipt(
            robot_id=body.robot_id, command_id=uuid.uuid4().hex, kind=body.kind, topic=topic
        )

    @app.get("/healthz/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/healthz/ready")
    async def ready(response: Response) -> dict[str, object]:
        checks = dict(publisher.readiness())
        ok = all(checks.values())
        ready_gauge.set(1 if ok else 0)
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ok else "degraded", "checks": checks}

    @app.get("/metrics")
    async def metrics() -> Response:
        ready_gauge.set(1 if all(publisher.readiness().values()) else 0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
