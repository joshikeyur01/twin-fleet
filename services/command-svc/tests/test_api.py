"""API tests with a fake publisher — no broker."""

from __future__ import annotations

from fastapi.testclient import TestClient

from command_svc.api import build_app
from command_svc.publisher import BrokerUnavailableError, Publisher
from contracts import JointCommand, command_topic


class FakePublisher(Publisher):
    """Publisher with the broker swapped for a switch."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.published: list[JointCommand] = []

    def readiness(self) -> dict[str, bool]:
        return {"mqtt": self.connected}

    async def publish(self, command: JointCommand) -> str:
        if not self.connected:
            raise BrokerUnavailableError("MQTT broker unavailable")
        self.published.append(command)
        return command_topic(command.robot_id)


def test_command_accepted() -> None:
    publisher = FakePublisher()
    client = TestClient(build_app(publisher))
    response = client.post("/command", json={"robot_id": "robot_1", "kind": "home"})
    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "home"
    assert body["robot_id"] == "robot_1"
    assert body["topic"] == "twin/robot_1/ur5/cmd/joints"  # namespaced by robot_id
    assert len(body["command_id"]) == 32  # uuid4 hex
    assert len(publisher.published) == 1


def test_broker_down_is_503_not_fake_success() -> None:
    client = TestClient(build_app(FakePublisher(connected=False)))
    response = client.post("/command", json={"robot_id": "robot_1", "kind": "home"})
    assert response.status_code == 503
    assert response.json()["detail"] == "MQTT broker unavailable"


def test_contract_violation_is_422() -> None:
    publisher = FakePublisher()
    client = TestClient(build_app(publisher))
    response = client.post("/command", json={"robot_id": "robot_1", "kind": "move_joints"})
    assert response.status_code == 422
    assert "move_joints requires positions" in str(response.json())
    assert publisher.published == []  # rejected before the handler ran


def test_missing_robot_id_is_422() -> None:
    publisher = FakePublisher()
    client = TestClient(build_app(publisher))
    response = client.post("/command", json={"kind": "home"})
    assert response.status_code == 422  # robot_id is a required contract field
    assert publisher.published == []


def test_bad_robot_id_is_422() -> None:
    publisher = FakePublisher()
    client = TestClient(build_app(publisher))
    response = client.post("/command", json={"robot_id": "ur5", "kind": "home"})
    assert response.status_code == 422  # must match robot_<n>
    assert publisher.published == []


def test_readiness_reflects_publisher() -> None:
    publisher = FakePublisher()
    client = TestClient(build_app(publisher))
    assert client.get("/healthz/ready").status_code == 200
    publisher.connected = False
    response = client.get("/healthz/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"mqtt": False}}
    assert client.get("/healthz/live").status_code == 200  # alive regardless
