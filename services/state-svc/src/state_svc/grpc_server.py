"""The gRPC face of state-svc: TwinState assembly, per-robot fan-out, servicer.

Fleet stream semantics (state.proto): every subscriber holds a latest-wins
mailbox keyed *per robot_id* — at most one pending frame per robot, so a lagging
client loses intermediate states for a robot instead of growing a queue (drop,
don't buffer), and one busy robot never starves the others. GetState selects a
twin by robot_id; StreamState streams the whole fleet, or one robot if filtered.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import grpc

from contracts import ASSET_TYPE, SCHEMA_VERSION
from contracts.gen import state_pb2, state_pb2_grpc
from state_svc.kinematics import forward
from state_svc.window import RollingWindow


class _Subscriber:
    """Latest-wins-per-robot mailbox: at most one pending frame per robot_id."""

    def __init__(self) -> None:
        self._pending: dict[str, state_pb2.TwinState] = {}
        self._event = asyncio.Event()

    def offer(self, state: state_pb2.TwinState) -> None:
        # A newer frame replaces the pending one for that robot; never queues.
        self._pending[state.robot_id] = state
        self._event.set()

    async def drain(self) -> list[state_pb2.TwinState]:
        """Block until at least one frame is pending, then take all pending frames.

        The section after the await has no further awaits, so under asyncio's
        cooperative scheduling no offer() can interleave — the snapshot-and-clear
        is atomic and nothing is lost."""
        await self._event.wait()
        batch = list(self._pending.values())
        self._pending.clear()
        self._event.clear()
        return batch


class StateHub:
    """Per-robot latest state + latest-wins-per-robot fan-out to subscribers."""

    def __init__(self) -> None:
        self._latest: dict[str, state_pb2.TwinState] = {}
        self._subscribers: set[_Subscriber] = set()

    def latest_for(self, robot_id: str) -> state_pb2.TwinState | None:
        return self._latest.get(robot_id)

    def robots(self) -> list[str]:
        return sorted(self._latest)

    def publish(self, state: state_pb2.TwinState) -> None:
        self._latest[state.robot_id] = state
        for sub in self._subscribers:
            sub.offer(state)

    @contextmanager
    def subscribe(self) -> Iterator[_Subscriber]:
        sub = _Subscriber()
        self._subscribers.add(sub)
        try:
            yield sub
        finally:
            self._subscribers.discard(sub)


def build_state(
    robot_id: str, window: RollingWindow, rms_window_s: float
) -> state_pb2.TwinState | None:
    """Window snapshot + forward kinematics → one TwinState for one robot, or
    None if that robot's window is not yet complete."""
    snapshots = window.snapshot()
    if snapshots is None:
        return None
    pose = forward([snap.position_rad for snap in snapshots])
    state = state_pb2.TwinState(
        schema_version=SCHEMA_VERSION,
        asset=ASSET_TYPE,
        robot_id=robot_id,
        stamp_ns=window.stamp_ns,
        rms_window_s=rms_window_s,
    )
    state.end_effector.position_m.x = pose.x
    state.end_effector.position_m.y = pose.y
    state.end_effector.position_m.z = pose.z
    state.end_effector.orientation.x = pose.qx
    state.end_effector.orientation.y = pose.qy
    state.end_effector.orientation.z = pose.qz
    state.end_effector.orientation.w = pose.qw
    for snap in snapshots:
        state.joints.add(
            name=snap.name,
            position_rad=snap.position_rad,
            velocity_rad_s=snap.velocity_rad_s,
            effort_nm=snap.effort_nm,
            velocity_rms=snap.velocity_rms,
        )
    return state


class StateServicer(state_pb2_grpc.StateServiceServicer):
    def __init__(self, hub: StateHub) -> None:
        self._hub = hub

    async def GetState(  # noqa: N802  # gRPC method names come from the proto
        self,
        request: state_pb2.GetStateRequest,
        context: grpc.aio.ServicerContext[state_pb2.GetStateRequest, state_pb2.TwinState],
    ) -> state_pb2.TwinState:
        await self._check_asset(request.asset, context)
        if not request.robot_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "robot_id is required")
        state = self._hub.latest_for(request.robot_id)
        if state is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"no telemetry for {request.robot_id!r} yet",
            )
        assert state is not None  # abort() raises; mypy can't see that
        return state

    async def StreamState(  # noqa: N802  # gRPC method names come from the proto
        self,
        request: state_pb2.StreamStateRequest,
        context: grpc.aio.ServicerContext[state_pb2.StreamStateRequest, state_pb2.TwinState],
    ) -> AsyncIterator[state_pb2.TwinState]:
        await self._check_asset(request.asset, context)
        wanted = request.robot_id  # "" = the whole fleet
        # Decimation is per robot: max_rate_hz caps each robot independently, so
        # a fast robot never uses up another's rate budget.
        min_interval = 1.0 / request.max_rate_hz if request.max_rate_hz > 0 else 0.0
        last_sent: dict[str, float] = {}
        with self._hub.subscribe() as sub:
            while True:
                for state in await sub.drain():
                    if wanted and state.robot_id != wanted:
                        continue
                    now = time.monotonic()
                    if now - last_sent.get(state.robot_id, float("-inf")) < min_interval:
                        continue
                    last_sent[state.robot_id] = now
                    yield state

    async def _check_asset(
        self, requested: str, context: grpc.aio.ServicerContext[Any, Any]
    ) -> None:
        if requested and requested != ASSET_TYPE:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"unknown asset {requested!r}; this fleet is {ASSET_TYPE!r}",
            )


def build_server(hub: StateHub, port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    state_pb2_grpc.add_StateServiceServicer_to_server(StateServicer(hub), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    return server
