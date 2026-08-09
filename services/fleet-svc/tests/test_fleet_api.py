"""fleet-svc tests: registry liveness, fan-out accounting, and the API.

No broker and no real command-svc — discovery is driven by calling the registry's
observe hook directly, and command-svc is a MockTransport that returns 202 or 503
per robot. The clock is monkeypatched so liveness is deterministic.
"""

from __future__ import annotations

import json
import time

import httpx2
from fastapi.testclient import TestClient

from contracts import CommandKind, FleetCommand, FleetCommandStatus
from fleet_svc.api import build_app
from fleet_svc.config import FleetConfig
from fleet_svc.fanout import FanOut
from fleet_svc.registry import Registry

TELEM = "twin/{rid}/ur5/joint/elbow_joint/position"


def _config(**over: object) -> FleetConfig:
    base: dict[str, object] = dict(
        mqtt_host="x",
        mqtt_port=1883,
        command_svc_url="http://command-svc:8003",
        http_port=8005,
        liveness_timeout_s=3.0,
        fanout_concurrency=8,
    )
    base.update(over)
    return FleetConfig(**base)  # type: ignore[arg-type]


def _mock_fanout(
    config: FleetConfig, failing: set[str] | None = None, unreachable: bool = False
) -> FanOut:
    fail = failing or set()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if unreachable:
            raise httpx2.ConnectError("connection refused")
        body = json.loads(request.content)
        rid = body["robot_id"]
        if rid in fail:
            return httpx2.Response(503, json={"detail": "MQTT broker unavailable"})
        return httpx2.Response(202, json={"command_id": rid + "cmd", "robot_id": rid})

    fanout = FanOut(config)
    fanout._client = httpx2.AsyncClient(
        base_url=config.command_svc_url, transport=httpx2.MockTransport(handler)
    )
    return fanout


class TestRegistry:
    def test_not_ready_before_connecting(self) -> None:
        assert Registry(_config()).readiness() == {"mqtt": False}

    def test_enroll_and_liveness_window(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        reg = Registry(_config(liveness_timeout_s=3.0))
        reg._observe(TELEM.format(rid="robot_1"), b"{}")
        reg._observe(TELEM.format(rid="robot_2"), b"{}")
        assert reg.live_robots() == ["robot_1", "robot_2"]
        clock[0] += 5.0  # both age past the 3s window
        assert reg.live_robots() == []

    def test_last_will_drops_immediately(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        reg = Registry(_config())
        reg._observe(TELEM.format(rid="robot_1"), b"{}")
        assert reg.live_robots() == ["robot_1"]
        reg._observe("twin/robot_1/ur5/status", b"offline")  # the Last-Will
        assert reg.live_robots() == []

    def test_foreign_topic_ignored(self) -> None:
        reg = Registry(_config())
        reg._observe("weather/london/temp", b"20")
        assert reg.live_robots() == []

    def test_snapshot_only_lists_live(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        reg = Registry(_config())
        reg._observe(TELEM.format(rid="robot_1"), b"{}")
        snap = reg.snapshot()
        assert snap.size == 1
        assert snap.robots[0].robot_id == "robot_1"
        assert snap.robots[0].ready is True


class TestFanOut:
    async def test_all_dispatched_is_all_ok(self) -> None:
        fanout = _mock_fanout(_config())
        result = await fanout.dispatch(FleetCommand(kind=CommandKind.HOME), ["robot_1", "robot_2"])
        await fanout.close()
        assert result.all_ok
        assert (result.total, result.ok, result.failed) == (2, 2, 0)

    async def test_one_dispatch_fails_others_proceed(self) -> None:
        # The canonical case: five robots, command-svc 503s for robot_3.
        fanout = _mock_fanout(_config(), failing={"robot_3"})
        robots = [f"robot_{i}" for i in range(1, 6)]
        result = await fanout.dispatch(FleetCommand(kind=CommandKind.HOME), robots)
        await fanout.close()
        assert (result.total, result.ok, result.failed) == (5, 4, 1)
        assert not result.all_ok
        failed = [r for r in result.results if r.status is FleetCommandStatus.FAILED]
        assert [r.robot_id for r in failed] == ["robot_3"]  # reported, not dropped
        assert failed[0].detail is not None and "503" in failed[0].detail

    async def test_unreachable_command_svc_fails_every_entry(self) -> None:
        fanout = _mock_fanout(_config(), unreachable=True)
        result = await fanout.dispatch(FleetCommand(kind=CommandKind.HOME), ["robot_1", "robot_2"])
        await fanout.close()
        assert result.failed == 2  # no fake successes when command-svc is down
        assert all(r.status is FleetCommandStatus.FAILED for r in result.results)


def _client(reg: Registry, fanout: FanOut) -> TestClient:
    return TestClient(build_app(reg, fanout))


class TestApi:
    def _live_registry(self, monkeypatch, n: int) -> Registry:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        reg = Registry(_config())
        reg._mqtt_connected = True
        for i in range(1, n + 1):
            reg._observe(TELEM.format(rid=f"robot_{i}"), b"{}")
        return reg

    def test_get_fleet_lists_live(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        reg = self._live_registry(monkeypatch, 3)
        body = _client(reg, _mock_fanout(_config())).get("/fleet").json()
        assert body["size"] == 3
        assert {r["robot_id"] for r in body["robots"]} == {"robot_1", "robot_2", "robot_3"}

    def test_command_all_ok_is_200(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        reg = self._live_registry(monkeypatch, 2)
        resp = _client(reg, _mock_fanout(_config())).post("/fleet/command", json={"kind": "home"})
        assert resp.status_code == 200
        assert resp.json()["ok"] == 2

    def test_command_mixed_is_207_and_reports_failure(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        reg = self._live_registry(monkeypatch, 5)
        client = _client(reg, _mock_fanout(_config(), failing={"robot_3"}))
        resp = client.post("/fleet/command", json={"kind": "home"})
        assert resp.status_code == 207  # Multi-Status
        body = resp.json()
        assert (body["ok"], body["failed"]) == (4, 1)
        failed = [r for r in body["results"] if r["status"] == "failed"]
        assert failed[0]["robot_id"] == "robot_3"  # named, not silently dropped

    def test_readiness_tracks_mqtt(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        reg = Registry(_config())
        client = _client(reg, _mock_fanout(_config()))
        assert client.get("/healthz/ready").status_code == 503  # mqtt not connected
        reg._mqtt_connected = True
        assert client.get("/healthz/ready").status_code == 200
        assert client.get("/healthz/live").status_code == 200
