"""Unit tests for kinematics, the rolling window, the per-robot hub, and
TwinState assembly — no broker, no gRPC server."""

from __future__ import annotations

import math

import pytest

from contracts import UR5_JOINT_NAMES, JointField, JointTelemetry
from state_svc.grpc_server import StateHub, build_state
from state_svc.kinematics import forward
from state_svc.window import RollingWindow

# Closed-form zero-configuration pose, derivable by hand from the DH constants:
# x = a2 + a3, y = -(d4 + d6), z = d1 - d5.
ZERO_POSE = (-0.81725, -0.19145, -0.005491)


class TestKinematics:
    def test_zero_configuration_matches_closed_form(self) -> None:
        pose = forward([0.0] * 6)
        assert pose.x == pytest.approx(ZERO_POSE[0], abs=1e-12)
        assert pose.y == pytest.approx(ZERO_POSE[1], abs=1e-12)
        assert pose.z == pytest.approx(ZERO_POSE[2], abs=1e-12)

    @pytest.mark.parametrize(
        "angles",
        [
            [0.1, -1.2, 2.0, 0.5, -0.9, 3.0],
            [math.pi, 0, 0, math.pi, 0, 0],
            [-2.8, 1.4, -0.3, 2.2, 1.1, -1.7],
        ],
    )
    def test_quaternion_stays_unit_norm(self, angles: list[float]) -> None:
        q = forward(angles)
        norm = math.sqrt(q.qx**2 + q.qy**2 + q.qz**2 + q.qw**2)
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_wrong_arity_rejected(self) -> None:
        with pytest.raises(ValueError, match="expected 6 joint angles"):
            forward([0.0] * 5)


def _fill(
    window: RollingWindow, robot_id: str = "robot_1", stamp_ns: int = 1000, velocity: float = 0.0
) -> None:
    for joint in UR5_JOINT_NAMES:
        for field in JointField:
            value = velocity if field is JointField.VELOCITY else 0.0
            window.observe(
                joint, field, JointTelemetry(robot_id=robot_id, value=value, stamp_ns=stamp_ns)
            )


def _filled_state(robot_id: str) -> object:
    window = RollingWindow(window_s=2.0)
    _fill(window, robot_id=robot_id)
    state = build_state(robot_id, window, 2.0)
    assert state is not None
    return state


class TestRollingWindow:
    def test_incomplete_window_has_no_snapshot(self) -> None:
        window = RollingWindow(window_s=2.0)
        assert window.snapshot() is None
        window.observe(
            "elbow_joint",
            JointField.POSITION,
            JointTelemetry(robot_id="robot_1", value=1.0, stamp_ns=1),
        )
        assert window.snapshot() is None  # five joints still missing fields

    def test_snapshot_ordered_as_contract(self) -> None:
        window = RollingWindow(window_s=2.0)
        _fill(window)
        snapshots = window.snapshot()
        assert snapshots is not None
        assert [s.name for s in snapshots] == list(UR5_JOINT_NAMES)

    def test_velocity_rms(self) -> None:
        window = RollingWindow(window_s=10.0)
        _fill(window, stamp_ns=1_000)
        joint = UR5_JOINT_NAMES[0]
        window.observe(
            joint,
            JointField.VELOCITY,
            JointTelemetry(robot_id="robot_1", value=3.0, stamp_ns=2_000),
        )
        window.observe(
            joint,
            JointField.VELOCITY,
            JointTelemetry(robot_id="robot_1", value=4.0, stamp_ns=3_000),
        )
        snapshots = window.snapshot()
        assert snapshots is not None
        # samples: 0.0 (fill), 3.0, 4.0 -> sqrt((0+9+16)/3)
        assert snapshots[0].velocity_rms == pytest.approx(math.sqrt(25 / 3))

    def test_window_prunes_by_telemetry_time_not_wall_clock(self) -> None:
        window = RollingWindow(window_s=2.0)
        joint = UR5_JOINT_NAMES[0]
        second = 1_000_000_000
        window.observe(
            joint, JointField.VELOCITY, JointTelemetry(robot_id="robot_1", value=9.0, stamp_ns=0)
        )
        window.observe(
            joint,
            JointField.VELOCITY,
            JointTelemetry(robot_id="robot_1", value=1.0, stamp_ns=3 * second),
        )
        # The 9.0 sample is 3 sim-seconds older than the newest: outside the 2s window.
        assert window._velocity_rms(joint) == pytest.approx(1.0)

    def test_unknown_joint_is_not_state(self) -> None:
        window = RollingWindow(window_s=2.0)
        _fill(window)
        window.observe(
            "phantom_joint",
            JointField.POSITION,
            JointTelemetry(robot_id="robot_1", value=9.9, stamp_ns=99),
        )
        snapshots = window.snapshot()
        assert snapshots is not None
        assert all(s.name != "phantom_joint" for s in snapshots)


class TestBuildState:
    def test_incomplete_window_builds_nothing(self) -> None:
        assert build_state("robot_1", RollingWindow(window_s=2.0), 2.0) is None

    def test_build_state_matches_kinematics_and_tags_robot(self) -> None:
        window = RollingWindow(window_s=2.0)
        _fill(window, robot_id="robot_4", stamp_ns=42)
        state = build_state("robot_4", window, 2.0)
        assert state is not None
        assert state.robot_id == "robot_4"
        assert state.asset == "ur5"
        assert state.stamp_ns == 42
        assert state.rms_window_s == 2.0
        assert len(state.joints) == 6
        assert state.end_effector.position_m.x == pytest.approx(ZERO_POSE[0])


class TestStateHub:
    def test_latest_is_kept_per_robot(self) -> None:
        hub = StateHub()
        assert hub.latest_for("robot_1") is None
        s1, s2 = _filled_state("robot_1"), _filled_state("robot_2")
        hub.publish(s1)  # type: ignore[arg-type]
        hub.publish(s2)  # type: ignore[arg-type]
        assert hub.latest_for("robot_1") is s1
        assert hub.latest_for("robot_2") is s2
        assert hub.robots() == ["robot_1", "robot_2"]

    async def test_subscriber_latest_wins_per_robot(self) -> None:
        hub = StateHub()
        a, b = _filled_state("robot_1"), _filled_state("robot_1")
        with hub.subscribe() as sub:
            hub.publish(a)  # type: ignore[arg-type]
            hub.publish(b)  # type: ignore[arg-type]  # replaces a; never queues
            batch = await sub.drain()
        assert batch == [b]  # only the latest frame for robot_1

    async def test_subscriber_streams_every_robot(self) -> None:
        hub = StateHub()
        s1, s2 = _filled_state("robot_1"), _filled_state("robot_2")
        with hub.subscribe() as sub:
            hub.publish(s1)  # type: ignore[arg-type]
            hub.publish(s2)  # type: ignore[arg-type]
            batch = await sub.drain()
        assert {s.robot_id for s in batch} == {"robot_1", "robot_2"}

    def test_unsubscribe_stops_delivery(self) -> None:
        hub = StateHub()
        s1 = _filled_state("robot_1")
        with hub.subscribe():
            pass  # leave the context: the subscriber is discarded
        hub.publish(s1)  # type: ignore[arg-type]  # must not touch the dead subscriber
        assert hub.latest_for("robot_1") is s1
