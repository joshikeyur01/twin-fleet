"""End-to-end tests against the running compose stack (`just up`).

Marked slow + fleet: they need Docker and a real network, launch simulator
containers themselves, and skip when the stack is not running — so `just test`
stays green on a laptop without the stack up. Run them with `just up` first, or
`uv run pytest -m fleet`.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request

import grpc
import pytest

from contracts.gen import state_pb2, state_pb2_grpc

pytestmark = [pytest.mark.slow, pytest.mark.fleet]

SERVICES = {
    "telemetry-svc": 8001,
    "state-svc": 8002,
    "command-svc": 8003,
    "viz-svc": 8004,
    "fleet-svc": 8005,
}
FLEET_URL = "http://localhost:8005"
GRPC_TARGET = "localhost:50051"
NETWORK = "twin-fleet_default"
SIM_IMAGE = "twin-fleet-simulator"
# High ids so the test's robots don't collide with an operator's `just fleet`
# (which uses robot_1..N) — a colliding robot_id kicks the other MQTT client.
ROBOT_IDS = ["robot_101", "robot_102", "robot_103"]


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(url: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _stack_is_up() -> bool:
    # Must be TWIN-FLEET specifically: fleet-svc's /fleet endpoint is the tell
    # (twin-anomaly reuses ports 8001-8005 but serves a different :8005).
    try:
        status, body = _get(f"{FLEET_URL}/fleet")
        if status != 200 or "size" not in json.loads(body):
            return False
        return all(_get(f"http://localhost:{p}/healthz/live")[0] == 200 for p in SERVICES.values())
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _fleet_size() -> int:
    status, body = _get(f"{FLEET_URL}/fleet")
    return int(json.loads(body)["size"]) if status == 200 else -1


def _fleet_robot_ids() -> set[str]:
    status, body = _get(f"{FLEET_URL}/fleet")
    return {r["robot_id"] for r in json.loads(body)["robots"]} if status == 200 else set()


def _launch_robot(robot_id: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            f"twin-fleet-{robot_id}",
            "--network",
            NETWORK,
            "-e",
            "MQTT_HOST=mosquitto",
            "-e",
            f"ROBOT_ID={robot_id}",
            "-e",
            "TELEMETRY_HZ=50",
            SIM_IMAGE,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _kill_robot(robot_id: str) -> None:
    subprocess.run(
        ["docker", "kill", f"twin-fleet-{robot_id}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_until(predicate, timeout_s: float, poll_s: float = 0.5) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


@pytest.fixture(autouse=True, scope="module")
def require_stack() -> None:
    if not _stack_is_up():
        pytest.skip("compose stack not running — `just up` first")


@pytest.fixture(scope="module")
def fleet() -> list[str]:  # type: ignore[misc]
    for robot_id in ROBOT_IDS:
        _launch_robot(robot_id)
    # Wait for OUR specific robots, not just a count — an operator may already
    # have a fleet running, so a size check would pass before ours are discovered.
    if not _wait_until(lambda: set(ROBOT_IDS).issubset(_fleet_robot_ids()), timeout_s=15):
        for robot_id in ROBOT_IDS:
            _kill_robot(robot_id)
        pytest.fail(f"our robots never joined the fleet (saw {sorted(_fleet_robot_ids())})")
    yield ROBOT_IDS
    for robot_id in ROBOT_IDS:
        _kill_robot(robot_id)


def test_all_services_ready() -> None:
    for name, port in SERVICES.items():
        status, _ = _get(f"http://localhost:{port}/healthz/ready")
        assert status == 200, f"{name} not ready ({status})"


def test_fleet_lists_launched_robots(fleet: list[str]) -> None:
    status, body = _get(f"{FLEET_URL}/fleet")
    assert status == 200
    listed = {r["robot_id"] for r in json.loads(body)["robots"]}
    assert set(fleet).issubset(listed)


def test_state_svc_derives_each_robot(fleet: list[str]) -> None:
    with grpc.insecure_channel(GRPC_TARGET) as channel:
        stub = state_pb2_grpc.StateServiceStub(channel)
        for robot_id in fleet:
            # state-svc needs a full window; retry a few seconds.
            def has_state(rid: str = robot_id) -> bool:
                try:
                    state = stub.GetState(state_pb2.GetStateRequest(robot_id=rid), timeout=2)
                    return len(state.joints) == 6 and state.robot_id == rid
                except grpc.RpcError:
                    return False

            assert _wait_until(has_state, timeout_s=8), f"no state for {robot_id}"


def test_coordinated_home_moves_every_live_robot(fleet: list[str]) -> None:
    status, body = _post(f"{FLEET_URL}/fleet/command", {"kind": "home"})
    assert status == 200  # all dispatched
    # Robust to other robots the operator may have running: every live robot
    # dispatched, and at least our fleet is among them.
    assert body["failed"] == 0
    assert body["ok"] == body["total"]
    assert body["ok"] >= len(fleet)
    ids = {r["robot_id"] for r in body["results"]}
    assert set(fleet).issubset(ids)


def test_killing_one_robot_leaves_the_fleet() -> None:
    # A self-contained, high-id robot so this test disturbs neither the module
    # fleet nor an operator's `just fleet`.
    victim = "robot_199"
    _launch_robot(victim)
    try:
        assert _wait_until(lambda: victim in _fleet_robot_ids(), timeout_s=15), (
            "victim never joined the fleet"
        )
        _kill_robot(victim)  # Last-Will should drop it fast
        assert _wait_until(lambda: victim not in _fleet_robot_ids(), timeout_s=15), (
            "victim did not leave the fleet after kill"
        )
    finally:
        _kill_robot(victim)
