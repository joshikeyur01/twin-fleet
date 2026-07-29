"""Shared contracts for twin-fleet.

The single source of truth for every cross-service payload: Pydantic models for
MQTT/REST (``contracts.models``), fleet coordination shapes (``contracts.fleet``),
and generated protobuf/gRPC stubs (``contracts.gen``). Services import shapes
from here and never define their own — CI enforces it.
"""

from contracts.fleet import (
    FleetCommand,
    FleetCommandResult,
    FleetCommandStatus,
    FleetSnapshot,
    RobotCommandResult,
    RobotStatus,
)
from contracts.models import (
    ASSET_TYPE,
    ROBOT_ID_PATTERN,
    SCHEMA_VERSION,
    UR5_JOINT_NAMES,
    CommandKind,
    CommandReceipt,
    JointCommand,
    JointField,
    JointTelemetry,
    command_topic,
    fleet_wildcard,
    parse_telemetry_topic,
    robot_id_from_topic,
    status_topic,
    telemetry_topic,
    telemetry_wildcard,
)

__all__ = [
    "ASSET_TYPE",
    "ROBOT_ID_PATTERN",
    "SCHEMA_VERSION",
    "UR5_JOINT_NAMES",
    "CommandKind",
    "CommandReceipt",
    "FleetCommand",
    "FleetCommandResult",
    "FleetCommandStatus",
    "FleetSnapshot",
    "JointCommand",
    "JointField",
    "JointTelemetry",
    "RobotCommandResult",
    "RobotStatus",
    "command_topic",
    "fleet_wildcard",
    "parse_telemetry_topic",
    "robot_id_from_topic",
    "status_topic",
    "telemetry_topic",
    "telemetry_wildcard",
]
