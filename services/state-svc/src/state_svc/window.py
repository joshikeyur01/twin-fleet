"""Rolling joint-state window.

The bridge publishes one message per joint per field; this module assembles
that stream back into coherent per-joint state and keeps a time-bounded
velocity history for RMS. Time is telemetry time (stamp_ns), never wall
clock — a paused sim must not decay its own RMS.

Single-task use only: the MQTT consumer owns it. No locking by design.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from contracts import UR5_JOINT_NAMES, JointField, JointTelemetry


@dataclass(slots=True)
class JointSnapshot:
    name: str
    position_rad: float
    velocity_rad_s: float
    effort_nm: float
    velocity_rms: float


class RollingWindow:
    def __init__(self, window_s: float, joints: Sequence[str] = UR5_JOINT_NAMES) -> None:
        self._window_ns = int(window_s * 1e9)
        self._joints = tuple(joints)
        self._latest: dict[tuple[str, JointField], float] = {}
        self._velocities: dict[str, deque[tuple[int, float]]] = {
            joint: deque() for joint in self._joints
        }
        self._stamp_ns = 0

    @property
    def stamp_ns(self) -> int:
        """Stamp of the newest telemetry observed."""
        return self._stamp_ns

    def observe(self, joint: str, field: JointField, sample: JointTelemetry) -> None:
        """Fold one validated telemetry message into the window."""
        if joint not in self._velocities:
            return  # joints outside the contract vocabulary are not state
        self._latest[(joint, field)] = sample.value
        self._stamp_ns = max(self._stamp_ns, sample.stamp_ns)
        if field is JointField.VELOCITY:
            history = self._velocities[joint]
            history.append((sample.stamp_ns, sample.value))
            cutoff = self._stamp_ns - self._window_ns
            while history and history[0][0] < cutoff:
                history.popleft()

    def snapshot(self) -> list[JointSnapshot] | None:
        """Coherent view of every joint, or None until each field has arrived once."""
        snapshots: list[JointSnapshot] = []
        for joint in self._joints:
            try:
                position = self._latest[(joint, JointField.POSITION)]
                velocity = self._latest[(joint, JointField.VELOCITY)]
                effort = self._latest[(joint, JointField.EFFORT)]
            except KeyError:
                return None
            snapshots.append(
                JointSnapshot(joint, position, velocity, effort, self._velocity_rms(joint))
            )
        return snapshots

    def _velocity_rms(self, joint: str) -> float:
        history = self._velocities[joint]
        if not history:
            return 0.0
        return math.sqrt(sum(v * v for _, v in history) / len(history))
