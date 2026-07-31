"""Synthetic UR5 motion — a deterministic stand-in for Gazebo physics.

Each joint oscillates as a sine around a movable centre: idle motion keeps the
arm visibly alive, and a command moves the centre (home → 0, move_joints → the
requested angles), so a coordinated fleet command is visible as every arm
shifting at once. A per-robot phase offset makes twins distinguishable rather
than byte-identical clones. Pure and testable — no MQTT, no clock ownership.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from contracts import UR5_JOINT_NAMES

IDLE_AMPLITUDE_RAD = 0.3
IDLE_FREQUENCY_HZ = 0.2
_ANGULAR = 2 * math.pi * IDLE_FREQUENCY_HZ


def robot_phase(robot_id: str) -> float:
    """A stable per-robot phase offset derived from the robot ordinal."""
    ordinal = int(robot_id.removeprefix("robot_"))
    return (ordinal * 0.7) % (2 * math.pi)


@dataclass(slots=True)
class SyntheticArm:
    """Per-joint sine motion around a movable centre; commands move the centre."""

    phase: float
    _centers: dict[str, float] = field(
        default_factory=lambda: {joint: 0.0 for joint in UR5_JOINT_NAMES}
    )

    def home(self) -> None:
        """Send every joint centre home (0)."""
        for joint in self._centers:
            self._centers[joint] = 0.0

    def move(self, positions: dict[str, float]) -> None:
        """Move the centre of each named joint; unknown joints are ignored."""
        for joint, target in positions.items():
            if joint in self._centers:
                self._centers[joint] = target

    def _joint_phase(self, index: int) -> float:
        return self.phase + index * (math.pi / 3)

    def sample(self, joint: str, t: float) -> tuple[float, float, float]:
        """(position, velocity, effort) for one joint at time t seconds."""
        index = UR5_JOINT_NAMES.index(joint)
        ph = self._joint_phase(index)
        position = self._centers[joint] + IDLE_AMPLITUDE_RAD * math.sin(_ANGULAR * t + ph)
        velocity = IDLE_AMPLITUDE_RAD * _ANGULAR * math.cos(_ANGULAR * t + ph)
        effort = 2.0 * velocity  # synthetic torque, proportional to velocity
        return position, velocity, effort
