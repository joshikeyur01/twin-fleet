"""Contract tests: round-trips, namespacing, and evolution guards.

If a change here feels annoying, that is the point — these tests are the fence
around the wire format (inherited ADR-0003) and the namespacing rules (ADR-0002).
Deleting a model field, renumbering a proto field, or dropping robot_id must
break something here before it breaks a service.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts import (
    SCHEMA_VERSION,
    UR5_JOINT_NAMES,
    CommandKind,
    CommandReceipt,
    FleetCommand,
    FleetCommandResult,
    FleetCommandStatus,
    FleetSnapshot,
    JointCommand,
    JointField,
    JointTelemetry,
    RobotCommandResult,
    RobotStatus,
    command_topic,
    fleet_wildcard,
    parse_telemetry_topic,
    robot_id_from_topic,
    status_topic,
    telemetry_topic,
    telemetry_wildcard,
)
from contracts.gen import state_pb2


class TestJointTelemetry:
    def test_roundtrip(self) -> None:
        sample = JointTelemetry(robot_id="robot_1", value=1.57, stamp_ns=123_456_789)
        again = JointTelemetry.model_validate_json(sample.model_dump_json())
        assert again == sample

    def test_schema_version_is_2(self) -> None:
        # twin-fleet starts at v2; the v1->v2 change is the required robot_id.
        assert SCHEMA_VERSION == 2
        assert JointTelemetry(robot_id="robot_1", value=0.0, stamp_ns=0).schema_version == 2

    def test_missing_robot_id_rejected(self) -> None:
        # The one sanctioned break at the fork boundary: a fleet has no
        # anonymous telemetry (ADR-0002). A twin-services-style v1 payload fails.
        with pytest.raises(ValidationError):
            JointTelemetry.model_validate_json('{"value": 0.5, "stamp_ns": 42}')

    @pytest.mark.parametrize("bad", ["ur5", "robot_", "robotX", "robot_1a", "1", ""])
    def test_malformed_robot_id_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            JointTelemetry(robot_id=bad, value=0.0, stamp_ns=0)

    def test_tomorrows_producer_todays_consumer(self) -> None:
        # Unknown fields must be ignored, never rejected (additive evolution).
        sample = JointTelemetry.model_validate_json(
            '{"robot_id": "robot_1", "value": 0.5, "stamp_ns": 42,'
            ' "schema_version": 3, "torque_ripple": 0.1}'
        )
        assert sample.schema_version == 3

    def test_negative_stamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JointTelemetry(robot_id="robot_1", value=0.0, stamp_ns=-1)


class TestJointCommand:
    def test_home_takes_no_positions(self) -> None:
        assert JointCommand(robot_id="robot_1", kind=CommandKind.HOME).positions is None
        with pytest.raises(ValidationError, match="home takes no positions"):
            JointCommand(robot_id="robot_1", kind=CommandKind.HOME, positions={"elbow_joint": 1.0})

    def test_move_joints_requires_positions(self) -> None:
        with pytest.raises(ValidationError, match="move_joints requires positions"):
            JointCommand(robot_id="robot_1", kind=CommandKind.MOVE_JOINTS)
        cmd = JointCommand(
            robot_id="robot_1", kind=CommandKind.MOVE_JOINTS, positions={"elbow_joint": 1.0}
        )
        assert cmd.duration_s == 2.0  # default

    def test_duration_bounds(self) -> None:
        with pytest.raises(ValidationError):
            JointCommand(robot_id="robot_1", kind=CommandKind.HOME, duration_s=0)
        with pytest.raises(ValidationError):
            JointCommand(robot_id="robot_1", kind=CommandKind.HOME, duration_s=31)

    def test_rest_body_shape(self) -> None:
        # The JSON command-svc accepts: robot_id + kind.
        cmd = JointCommand.model_validate_json('{"robot_id": "robot_2", "kind": "home"}')
        assert cmd.kind is CommandKind.HOME
        assert cmd.robot_id == "robot_2"

    def test_receipt_roundtrip(self) -> None:
        receipt = CommandReceipt(
            robot_id="robot_1", command_id="ab" * 16, kind=CommandKind.HOME, topic="t"
        )
        assert CommandReceipt.model_validate_json(receipt.model_dump_json()) == receipt


class TestTopics:
    def test_build_parse_roundtrip(self) -> None:
        for joint in UR5_JOINT_NAMES:
            for field in JointField:
                topic = telemetry_topic("robot_3", joint, field)
                assert parse_telemetry_topic(topic) == ("robot_3", joint, field)

    @pytest.mark.parametrize(
        "bad",
        [
            "twin/robot_1/ur5/cmd/joints",
            "twin/robot_1/ur5/joint/elbow_joint",
            "twin/robot_1/ur5/joint/elbow_joint/torque",
            "twin/ur5/joint/elbow_joint/position",  # old twin-services scheme, no robot_id
            "twin/badid/ur5/joint/elbow_joint/position",  # robot_id not robot_<n>
            "other/robot_1/ur5/joint/elbow_joint/position",
            "",
        ],
    )
    def test_parse_rejects_non_telemetry(self, bad: str) -> None:
        with pytest.raises(ValueError, match="not a telemetry topic"):
            parse_telemetry_topic(bad)

    def test_wildcard_and_builders(self) -> None:
        assert telemetry_wildcard() == "twin/+/ur5/joint/+/+"
        assert fleet_wildcard() == "twin/+/ur5/#"
        assert command_topic("robot_1") == "twin/robot_1/ur5/cmd/joints"
        assert status_topic("robot_1") == "twin/robot_1/ur5/status"


class TestRobotIdFromTopic:
    @pytest.mark.parametrize(
        "topic",
        [
            "twin/robot_9/ur5/joint/elbow_joint/position",
            "twin/robot_9/ur5/cmd/joints",
            "twin/robot_9/ur5/status",
        ],
    )
    def test_extracts_from_any_ur5_topic(self, topic: str) -> None:
        assert robot_id_from_topic(topic) == "robot_9"

    @pytest.mark.parametrize("bad", ["twin/ur5/joint/x/position", "other/robot_1/ur5/x", ""])
    def test_rejects_foreign_topic(self, bad: str) -> None:
        with pytest.raises(ValueError, match="no robot_id in topic"):
            robot_id_from_topic(bad)

    def test_topic_envelope_crosscheck(self) -> None:
        # ADR-0002: consumers compare the topic's robot_id to the payload's.
        topic = telemetry_topic("robot_1", "elbow_joint", JointField.POSITION)
        payload = JointTelemetry(robot_id="robot_2", value=0.0, stamp_ns=0)
        assert robot_id_from_topic(topic) != payload.robot_id  # a mismatch is detectable


class TestProto:
    def test_twinstate_roundtrip(self) -> None:
        state = state_pb2.TwinState(
            schema_version=SCHEMA_VERSION,
            asset="ur5",
            robot_id="robot_4",
            stamp_ns=42,
            rms_window_s=2.0,
        )
        state.end_effector.position_m.x = -0.81725
        state.joints.add(name="elbow_joint", position_rad=1.57, velocity_rms=0.1)
        again = state_pb2.TwinState.FromString(state.SerializeToString())
        assert again == state
        assert again.robot_id == "robot_4"
        assert again.joints[0].name == "elbow_joint"

    def test_twinstate_field_numbers_are_locked(self) -> None:
        # Renumbering a proto field silently corrupts old payloads (ADR-0003).
        fields = {f.name: f.number for f in state_pb2.TwinState.DESCRIPTOR.fields}
        assert fields == {
            "schema_version": 1,
            "asset": 2,
            "stamp_ns": 3,
            "end_effector": 4,
            "joints": 5,
            "rms_window_s": 6,
            "robot_id": 7,  # added additively (ADR-0002)
        }

    def test_request_field_numbers_are_locked(self) -> None:
        get_fields = {f.name: f.number for f in state_pb2.GetStateRequest.DESCRIPTOR.fields}
        assert get_fields == {"asset": 1, "robot_id": 2}
        stream_fields = {f.name: f.number for f in state_pb2.StreamStateRequest.DESCRIPTOR.fields}
        assert stream_fields == {"asset": 1, "max_rate_hz": 2, "robot_id": 3}


class TestFleet:
    def test_fleet_command_validation(self) -> None:
        assert FleetCommand(kind=CommandKind.HOME).positions is None
        with pytest.raises(ValidationError, match="move_joints requires positions"):
            FleetCommand(kind=CommandKind.MOVE_JOINTS)

    def test_result_counts_are_derived(self) -> None:
        result = FleetCommandResult(
            kind=CommandKind.HOME,
            results=[
                RobotCommandResult(
                    robot_id="robot_1", status=FleetCommandStatus.OK, command_id="a"
                ),
                RobotCommandResult(
                    robot_id="robot_2", status=FleetCommandStatus.OK, command_id="b"
                ),
                RobotCommandResult(
                    robot_id="robot_3", status=FleetCommandStatus.FAILED, detail="unreachable"
                ),
            ],
        )
        assert (result.total, result.ok, result.failed) == (3, 2, 1)
        assert result.all_ok is False

    def test_all_ok_when_every_robot_succeeds(self) -> None:
        result = FleetCommandResult(
            kind=CommandKind.HOME,
            results=[
                RobotCommandResult(robot_id="robot_1", status=FleetCommandStatus.OK),
            ],
        )
        assert result.all_ok is True

    def test_dead_twin_is_a_failed_entry_not_dropped(self) -> None:
        # ADR-0004: a dead robot is reported, never silently omitted.
        result = FleetCommandResult(
            kind=CommandKind.HOME,
            results=[
                RobotCommandResult(
                    robot_id="robot_5", status=FleetCommandStatus.FAILED, detail="no such twin"
                )
            ],
        )
        assert result.failed == 1
        assert result.results[0].detail == "no such twin"

    def test_result_json_roundtrip_recomputes_counts(self) -> None:
        result = FleetCommandResult(
            kind=CommandKind.HOME,
            results=[RobotCommandResult(robot_id="robot_1", status=FleetCommandStatus.OK)],
        )
        dumped = result.model_dump_json()
        assert '"total":1' in dumped.replace(" ", "")  # computed field is serialised
        back = FleetCommandResult.model_validate_json(dumped)  # extra count keys ignored
        assert back.results == result.results
        assert back.total == 1


class TestFleetSnapshot:
    def test_size_is_derived(self) -> None:
        snap = FleetSnapshot(
            robots=[
                RobotStatus(robot_id="robot_1", last_seen_s=0.1, ready=True),
                RobotStatus(robot_id="robot_2", last_seen_s=5.0, ready=False),
            ]
        )
        assert snap.size == 2
        assert '"size":2' in snap.model_dump_json().replace(" ", "")

    def test_empty_fleet(self) -> None:
        assert FleetSnapshot(robots=[]).size == 0

    def test_bad_robot_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RobotStatus(robot_id="ur5", last_seen_s=0.0, ready=True)
