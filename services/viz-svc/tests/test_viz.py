"""Frame conversion and app tests with a fake stream — no gRPC, no browser."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from contracts import SCHEMA_VERSION
from contracts.gen import state_pb2
from viz_svc.app import build_app
from viz_svc.config import VizConfig
from viz_svc.stream import StateStream, to_frame


def _twin_state(robot_id: str = "robot_1") -> state_pb2.TwinState:
    state = state_pb2.TwinState(
        schema_version=SCHEMA_VERSION,
        asset="ur5",
        robot_id=robot_id,
        stamp_ns=1_500_000_000,
        rms_window_s=2.0,
    )
    state.end_effector.position_m.x = -0.8
    state.end_effector.orientation.w = 1.0
    state.joints.add(name="elbow_joint", position_rad=1.57, velocity_rms=0.1)
    return state


class TestToFrame:
    def test_shape_includes_robot_id(self) -> None:
        frame = json.loads(to_frame(_twin_state("robot_7")))
        assert frame["robot_id"] == "robot_7"
        assert frame["ee"]["pos"] == [-0.8, 0.0, 0.0]
        assert frame["ee"]["quat"] == [0.0, 0.0, 0.0, 1.0]
        assert frame["joints"] == [
            {"name": "elbow_joint", "position_rad": 1.57, "velocity_rms": 0.1}
        ]

    def test_stamp_is_milliseconds_not_nanoseconds(self) -> None:
        # Raw ns exceeds JSON's exact-integer range (2^53); the frame converts.
        frame = json.loads(to_frame(_twin_state()))
        assert frame["stamp_ms"] == 1_500.0
        assert "stamp_ns" not in frame


class FakeStream(StateStream):
    def __init__(self, ready: bool = True, frames: list[str] | None = None) -> None:
        self._ready_flag = ready
        self._frames = frames or []

    def readiness(self) -> dict[str, bool]:
        return {"state_grpc": self._ready_flag}

    async def frames(self) -> AsyncIterator[str]:
        for frame in self._frames:
            yield frame


def _client(stream: FakeStream, tmp_static: str = "/nonexistent") -> TestClient:
    config = VizConfig(state_grpc_target="unused:0", http_port=0, static_dir=tmp_static)
    return TestClient(build_app(config, stream))


class TestApp:
    def test_readiness_tracks_channel(self) -> None:
        assert _client(FakeStream(ready=True)).get("/healthz/ready").status_code == 200
        response = _client(FakeStream(ready=False)).get("/healthz/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"state_grpc": False}

    def test_missing_bundle_keeps_service_alive(self) -> None:
        client = _client(FakeStream(), tmp_static="/definitely/not/there")
        assert client.get("/healthz/live").status_code == 200

    def test_websocket_forwards_fleet_frames(self) -> None:
        frames = [to_frame(_twin_state("robot_1")), to_frame(_twin_state("robot_2"))]
        client = _client(FakeStream(frames=frames))
        with client.websocket_connect("/ws/state") as ws:
            assert json.loads(ws.receive_text())["robot_id"] == "robot_1"
            assert json.loads(ws.receive_text())["robot_id"] == "robot_2"
