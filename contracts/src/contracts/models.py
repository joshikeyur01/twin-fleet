"""Pydantic contracts for everything crossing MQTT or REST.

Every payload that crosses a service boundary is one of these models — raw
dicts stop at the transport callback. The gRPC side of the contract lives in
``contracts/proto``; this module is the JSON side. Fleet coordination shapes
(``FleetCommand``/``FleetCommandResult``) live in ``contracts.fleet``.

Namespacing (ADR-0002): every telemetry/command payload carries ``robot_id``,
and every topic embeds it too (``twin/<robot_id>/ur5/…``). Consumers subscribe
with the ``twin/+/ur5/#`` wildcard, extract ``robot_id`` from the topic, and
cross-check it against the envelope — a mismatch is a bug, not a silent accept.

Schema version: twin-fleet starts at ``SCHEMA_VERSION = 2``. The v1→v2 change is
the addition of the required ``robot_id`` field — the one sanctioned break at the
fork boundary, because a fleet has no anonymous telemetry. Forward from v2 the
inherited ADR-0003 rule resumes: additive-only, new fields carry defaults, and no
model here forbids extra fields (consumers ignore fields they don't know yet).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 2

# The asset *type* segment. Each robot is a UR5; ``robot_id`` identifies the
# instance, ``ASSET_TYPE`` the model. Constant for this repo.
ASSET_TYPE = "ur5"

# Robot identity format (ADR-0002): human-readable, sortable, one config owns
# the canonical set. Both the field validator and the topic parser use it.
ROBOT_ID_PATTERN = r"^robot_\d+$"

# Shared vocabulary: joint order is load-bearing (FK in state-svc, bone order
# in viz-svc), so it is a contract, not a service detail.
UR5_JOINT_NAMES: tuple[str, ...] = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)


class JointField(StrEnum):
    """The three per-joint telemetry channels a simulator publishes."""

    POSITION = "position"
    VELOCITY = "velocity"
    EFFORT = "effort"


# ─── topics ──────────────────────────────────────────────────────────────────

_TELEMETRY_TOPIC = re.compile(
    r"^twin/(?P<robot_id>robot_\d+)/ur5/joint/(?P<joint>[^/]+)"
    r"/(?P<field>position|velocity|effort)$"
)
_ROBOT_ID_IN_TOPIC = re.compile(r"^twin/(?P<robot_id>robot_\d+)/ur5/")


def telemetry_topic(robot_id: str, joint: str, field: JointField) -> str:
    """Topic a simulator publishes one ``JointTelemetry`` payload to."""
    return f"twin/{robot_id}/{ASSET_TYPE}/joint/{joint}/{field}"


def telemetry_wildcard() -> str:
    """Subscription filter matching every joint telemetry topic, all robots."""
    return f"twin/+/{ASSET_TYPE}/joint/+/+"


def fleet_wildcard() -> str:
    """Broad filter (telemetry + cmd + status) for fleet-svc's passive discovery."""
    return f"twin/+/{ASSET_TYPE}/#"


def parse_telemetry_topic(topic: str) -> tuple[str, str, JointField]:
    """Split a telemetry topic into (robot_id, joint, field); raise on anything else."""
    match = _TELEMETRY_TOPIC.match(topic)
    if match is None:
        raise ValueError(f"not a telemetry topic: {topic!r}")
    return match["robot_id"], match["joint"], JointField(match["field"])


def robot_id_from_topic(topic: str) -> str:
    """Extract ``robot_id`` from any ``twin/<robot_id>/ur5/…`` topic (joint, cmd,
    or status). Used by fleet-svc on the broad wildcard; raises on a foreign topic."""
    match = _ROBOT_ID_IN_TOPIC.match(topic)
    if match is None:
        raise ValueError(f"no robot_id in topic: {topic!r}")
    return match["robot_id"]


def command_topic(robot_id: str) -> str:
    """Topic command-svc publishes ``JointCommand`` setpoints to for one robot."""
    return f"twin/{robot_id}/{ASSET_TYPE}/cmd/joints"


def status_topic(robot_id: str) -> str:
    """Presence topic; a simulator's MQTT Last-Will lands here (ADR-0003)."""
    return f"twin/{robot_id}/{ASSET_TYPE}/status"


# ─── payloads ────────────────────────────────────────────────────────────────


class JointTelemetry(BaseModel):
    """One joint field at one instant — the payload on each telemetry topic.

    ``robot_id`` (v2) identifies the emitting twin and must equal the topic's
    ``robot_id`` segment; the consumer cross-checks. A simulator is the producer
    (there is no twin-hello bridge in twin-fleet)."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    robot_id: str = Field(..., pattern=ROBOT_ID_PATTERN)
    value: float
    stamp_ns: int = Field(..., ge=0)


class CommandKind(StrEnum):
    HOME = "home"
    MOVE_JOINTS = "move_joints"


class JointCommand(BaseModel):
    """A setpoint request for one robot: REST body at command-svc, then MQTT
    payload on ``twin/<robot_id>/ur5/cmd/joints``."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    robot_id: str = Field(..., pattern=ROBOT_ID_PATTERN)
    kind: CommandKind
    positions: dict[str, float] | None = Field(
        default=None, description="Target angle in radians, keyed by joint name."
    )
    duration_s: float = Field(default=2.0, gt=0, le=30)

    @model_validator(mode="after")
    def _positions_match_kind(self) -> JointCommand:
        if self.kind is CommandKind.MOVE_JOINTS and not self.positions:
            raise ValueError("move_joints requires positions")
        if self.kind is CommandKind.HOME and self.positions:
            raise ValueError("home takes no positions")
        return self


class CommandReceipt(BaseModel):
    """command-svc's REST response: which robot, what was accepted, where it went."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    robot_id: str = Field(..., pattern=ROBOT_ID_PATTERN)
    command_id: str = Field(..., description="uuid4 hex assigned by command-svc.")
    kind: CommandKind
    topic: str
