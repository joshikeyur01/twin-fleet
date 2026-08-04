"""UR5 forward kinematics.

Standard Denavit-Hartenberg chain with the published UR5 parameters. Pure
functions, numpy only, no service imports — this module is the numerically
testable core, asserted against closed-form poses in the test suite.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

_Matrix = npt.NDArray[np.float64]

# Published UR5 DH parameters (metres, radians), in joint order — the same
# order as contracts.UR5_JOINT_NAMES.
# Source: Universal Robots, "DH parameters for calculations of kinematics
# and dynamics" (UR5).
_A = (0.0, -0.425, -0.39225, 0.0, 0.0, 0.0)
_D = (0.089159, 0.0, 0.0, 0.10915, 0.09465, 0.0823)
_ALPHA = (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)


@dataclass(frozen=True, slots=True)
class Pose:
    """Position in metres, orientation as a unit quaternion (x, y, z, w)."""

    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float


def forward(joint_angles: Sequence[float]) -> Pose:
    """End-effector pose in the base frame for six joint angles (radians)."""
    if len(joint_angles) != len(_A):
        raise ValueError(f"expected {len(_A)} joint angles, got {len(joint_angles)}")
    transform: _Matrix = np.eye(4)
    for theta, d, a, alpha in zip(joint_angles, _D, _A, _ALPHA, strict=True):
        transform = transform @ _dh_transform(theta, d, a, alpha)
    qx, qy, qz, qw = _quaternion_from_rotation(transform[:3, :3])
    return Pose(
        x=float(transform[0, 3]),
        y=float(transform[1, 3]),
        z=float(transform[2, 3]),
        qx=qx,
        qy=qy,
        qz=qz,
        qw=qw,
    )


def _dh_transform(theta: float, d: float, a: float, alpha: float) -> _Matrix:
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(alpha), math.sin(alpha)
    return np.array(
        [
            [ct, -st * ca, st * sa, a * ct],
            [st, ct * ca, -ct * sa, a * st],
            [0.0, sa, ca, d],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _quaternion_from_rotation(r: _Matrix) -> tuple[float, float, float, float]:
    """Rotation matrix → unit quaternion, largest-pivot branch for stability."""
    trace = float(r[0, 0] + r[1, 1] + r[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return (
            float(r[2, 1] - r[1, 2]) / s,
            float(r[0, 2] - r[2, 0]) / s,
            float(r[1, 0] - r[0, 1]) / s,
            0.25 * s,
        )
    if r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + float(r[0, 0] - r[1, 1] - r[2, 2])) * 2.0
        return (
            0.25 * s,
            float(r[0, 1] + r[1, 0]) / s,
            float(r[0, 2] + r[2, 0]) / s,
            float(r[2, 1] - r[1, 2]) / s,
        )
    if r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + float(r[1, 1] - r[0, 0] - r[2, 2])) * 2.0
        return (
            float(r[0, 1] + r[1, 0]) / s,
            0.25 * s,
            float(r[1, 2] + r[2, 1]) / s,
            float(r[0, 2] - r[2, 0]) / s,
        )
    s = math.sqrt(1.0 + float(r[2, 2] - r[0, 0] - r[1, 1])) * 2.0
    return (
        float(r[0, 2] + r[2, 0]) / s,
        float(r[1, 2] + r[2, 1]) / s,
        0.25 * s,
        float(r[1, 0] - r[0, 1]) / s,
    )
