"""HTTP face of viz-svc: the static viewer, its WebSocket feed, and the
standard health endpoints, one app.

Route order matters: /ws/state and /healthz/* are registered before the
static mount at / so the SPA can't shadow them. If the frontend bundle is
missing (backend-only dev) the service still runs — the viewer 404s but
health and the WebSocket stay honest.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import grpc
import structlog
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Gauge, generate_latest

from viz_svc.config import VizConfig
from viz_svc.stream import StateStream

log = structlog.get_logger()


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


def build_app(config: VizConfig, stream: StateStream) -> FastAPI:
    app = FastAPI(title="viz-svc")
    ready_gauge = _READY.labels(service="viz-svc")

    @app.websocket("/ws/state")
    async def ws_state(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            async for frame in stream.frames():
                await websocket.send_text(frame)
        except WebSocketDisconnect:
            pass  # browser left; frames() cleans up via its finally block
        except grpc.aio.AioRpcError as exc:
            log.warning("state_stream_ended", code=exc.code().name)
            # 1011: server-side condition. The frontend retries on a timer.
            await websocket.close(code=1011, reason="state-svc unavailable")

    @app.get("/healthz/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/healthz/ready")
    async def ready(response: Response) -> dict[str, object]:
        checks = dict(stream.readiness())
        ok = all(checks.values())
        ready_gauge.set(1 if ok else 0)
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ok else "degraded", "checks": checks}

    @app.get("/metrics")
    async def metrics() -> Response:
        ready_gauge.set(1 if all(stream.readiness().values()) else 0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    static = Path(config.static_dir)
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="viewer")
    else:
        log.warning("frontend_bundle_missing", static_dir=config.static_dir)

    return app
