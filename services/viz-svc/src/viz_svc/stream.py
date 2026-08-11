"""gRPC → WebSocket: the browser's view of the fleet.

Each connected browser gets its own StreamState RPC over the whole fleet (empty
robot_id filter), decimated server-side to BROWSER_RATE_HZ per robot — state-svc
drops rather than buffers, so a slow tab cannot build a queue anywhere in the
pipeline. Every frame is tagged with robot_id so the viewer can key one thin arm
per robot. When state-svc dies the RPC raises, the WebSocket closes, and the
frontend reconnects on its own timer; readiness tracks the gRPC channel throughout.

Frames are compact JSON. Stamps are milliseconds-as-float because raw nanoseconds
exceed JSON's exact-integer range (2^53).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import grpc
import structlog
from prometheus_client import Counter, Gauge

from contracts.gen import state_pb2, state_pb2_grpc
from viz_svc.config import VizConfig

log = structlog.get_logger()

FRAMES = Counter("twin_viz_frames_total", "Frames forwarded to browsers.")
CLIENTS = Gauge("twin_viz_clients", "Currently connected WebSocket clients.")

BROWSER_RATE_HZ = 30.0  # per robot; plenty for a 3D scene, a fraction of telemetry rate


def to_frame(state: state_pb2.TwinState) -> str:
    """One TwinState → one JSON text frame for the browser, tagged by robot_id."""
    ee = state.end_effector
    return json.dumps(
        {
            "robot_id": state.robot_id,
            "stamp_ms": state.stamp_ns / 1e6,
            "ee": {
                "pos": [ee.position_m.x, ee.position_m.y, ee.position_m.z],
                "quat": [
                    ee.orientation.x,
                    ee.orientation.y,
                    ee.orientation.z,
                    ee.orientation.w,
                ],
            },
            "joints": [
                {
                    "name": joint.name,
                    "position_rad": joint.position_rad,
                    "velocity_rms": joint.velocity_rms,
                }
                for joint in state.joints
            ],
        }
    )


class StateStream:
    """Owns the channel to state-svc and reports its readiness."""

    def __init__(self, config: VizConfig) -> None:
        self._config = config
        # Default reconnect backoff grows toward 2 minutes while state-svc is
        # down, which reads as "stuck". Cap it: readiness should flip back
        # within a few seconds of the dependency returning.
        self._channel = grpc.aio.insecure_channel(
            config.state_grpc_target,
            options=[
                ("grpc.initial_reconnect_backoff_ms", 1000),
                ("grpc.max_reconnect_backoff_ms", 3000),
            ],
        )
        self._stub = state_pb2_grpc.StateServiceStub(self._channel)
        self._ready = False

    def readiness(self) -> dict[str, bool]:
        return {"state_grpc": self._ready}

    async def run(self) -> None:
        """Track channel connectivity forever; this is the readiness source.
        gRPC reconnects with its own backoff — we only observe."""
        while True:
            connectivity = self._channel.get_state(try_to_connect=True)
            self._ready = connectivity == grpc.ChannelConnectivity.READY
            await self._channel.wait_for_state_change(connectivity)

    async def frames(self) -> AsyncIterator[str]:
        """One browser's stream of JSON frames for the whole fleet. Raises
        AioRpcError when state-svc goes away; the caller closes the socket."""
        # Empty robot_id = every live robot; each frame carries its own robot_id.
        request = state_pb2.StreamStateRequest(max_rate_hz=BROWSER_RATE_HZ)
        CLIENTS.inc()
        try:
            async for state in self._stub.StreamState(request):
                FRAMES.inc()
                yield to_frame(state)
        finally:
            CLIENTS.dec()

    async def close(self) -> None:
        await self._channel.close()
