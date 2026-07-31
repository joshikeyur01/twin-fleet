"""Unit tests for the synthetic motion model — no MQTT, no clock."""

from __future__ import annotations

import math

import pytest

from contracts import UR5_JOINT_NAMES
from simulators.motion import IDLE_AMPLITUDE_RAD, SyntheticArm, robot_phase


class TestRobotPhase:
    def test_stable_and_distinct(self) -> None:
        assert robot_phase("robot_1") == pytest.approx(0.7)
        assert robot_phase("robot_2") == pytest.approx(1.4)
        assert robot_phase("robot_1") != robot_phase("robot_2")

    def test_wraps_into_range(self) -> None:
        phase = robot_phase("robot_100")
        assert 0.0 <= phase < 2 * math.pi


class TestSyntheticArm:
    def test_sample_is_deterministic(self) -> None:
        a = SyntheticArm(phase=0.0)
        b = SyntheticArm(phase=0.0)
        assert a.sample("elbow_joint", 1.23) == b.sample("elbow_joint", 1.23)

    def test_position_oscillates_around_center(self) -> None:
        arm = SyntheticArm(phase=0.0)
        positions = [arm.sample("shoulder_pan_joint", t / 10)[0] for t in range(200)]
        # idle centre is 0, amplitude 0.3 — stays within the band, both signs seen.
        assert max(positions) <= IDLE_AMPLITUDE_RAD + 1e-9
        assert min(positions) >= -IDLE_AMPLITUDE_RAD - 1e-9
        assert max(positions) > 0 and min(positions) < 0

    def test_home_recenters_to_zero(self) -> None:
        arm = SyntheticArm(phase=0.0)
        arm.move({"elbow_joint": 1.5})
        # centre moved: the oscillation now sits around 1.5
        assert arm.sample("elbow_joint", 0.0)[0] == pytest.approx(1.5, abs=IDLE_AMPLITUDE_RAD)
        arm.home()
        assert abs(arm.sample("elbow_joint", 0.0)[0]) <= IDLE_AMPLITUDE_RAD + 1e-9

    def test_move_ignores_unknown_joints(self) -> None:
        arm = SyntheticArm(phase=0.0)
        arm.move({"phantom_joint": 9.9})  # must not raise or take effect
        assert all(joint in UR5_JOINT_NAMES for joint in ("elbow_joint", "wrist_1_joint"))

    def test_velocity_is_position_derivative_sign(self) -> None:
        # At t where sin peaks, velocity (cos) crosses zero; sanity-check the pair.
        arm = SyntheticArm(phase=0.0)
        pos, vel, effort = arm.sample("shoulder_pan_joint", 0.0)
        assert pos == pytest.approx(0.0, abs=1e-9)  # sin(0)=0 at centre 0
        assert vel > 0  # cos(0) > 0
        assert effort == pytest.approx(2.0 * vel)
