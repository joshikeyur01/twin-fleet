"""The REST face of fleet-svc: GET /fleet, POST /fleet/command, and the standard
health endpoints, one app.

The request/response shapes are `contracts` models (FleetCommand, FleetSnapshot,
FleetCommandResult) — this layer adds transport concerns (status codes), never
payload shapes. A mixed fan-out returns 207 Multi-Status with every robot's
outcome explicit (ADR-0004). Readiness tracks the MQTT discovery connection only:
fleet-svc can list the fleet without command-svc, and a command-svc outage shows
up honestly as failed dispatch entries, not as fleet-svc being unready.
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Gauge, generate_latest

from contracts import FleetCommand, FleetCommandResult, FleetSnapshot
from fleet_svc.fanout import FanOut
from fleet_svc.registry import Registry


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


def build_app(registry: Registry, fanout: FanOut) -> FastAPI:
    app = FastAPI(title="fleet-svc")
    ready_gauge = _READY.labels(service="fleet-svc")

    @app.get("/fleet")
    async def get_fleet() -> FleetSnapshot:
        """Every robot currently within the liveness window (ADR-0003)."""
        return registry.snapshot()

    @app.post("/fleet/command")
    async def fleet_command(body: FleetCommand, response: Response) -> FleetCommandResult:
        """Fan a command out to every live robot; 200 if all dispatched, else 207
        with each robot's outcome explicit (ADR-0004)."""
        result = await fanout.dispatch(body, registry.live_robots())
        if not result.all_ok:
            response.status_code = status.HTTP_207_MULTI_STATUS
        return result

    @app.get("/healthz/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/healthz/ready")
    async def ready(response: Response) -> dict[str, object]:
        checks = dict(registry.readiness())
        ok = all(checks.values())
        ready_gauge.set(1 if ok else 0)
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ok else "degraded", "checks": checks}

    @app.get("/metrics")
    async def metrics() -> Response:
        registry.refresh_metrics()  # reconcile fleet gauges on scrape
        ready_gauge.set(1 if all(registry.readiness().values()) else 0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
