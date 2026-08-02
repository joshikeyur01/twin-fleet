"""Unit tests for the point conversion and readiness — no broker, no InfluxDB."""

from __future__ import annotations

from prometheus_client import REGISTRY

from telemetry_svc.config import TelemetryConfig
from telemetry_svc.ingest import Ingestor, _to_point


def _rejections(reason: str) -> float:
    value = REGISTRY.get_sample_value("twin_telemetry_rejected_total", {"reason": reason})
    return value or 0.0


class TestToPoint:
    def test_valid_message_becomes_point(self) -> None:
        point = _to_point(
            "twin/robot_3/ur5/joint/elbow_joint/position",
            '{"robot_id": "robot_3", "value": 1.57, "stamp_ns": 123}',
        )
        assert point is not None
        line = point.to_line_protocol()  # type: ignore[no-untyped-call]  # influx client is untyped
        # Tags are emitted in sorted key order: joint, metric, robot_id.
        assert line == (
            "joint_telemetry,joint=elbow_joint,metric=position,robot_id=robot_3 value=1.57 123"
        )

    def test_non_telemetry_topic_dropped(self) -> None:
        before = _rejections("topic")
        assert (
            _to_point(
                "twin/robot_3/ur5/cmd/joints", '{"robot_id": "robot_3", "value": 1, "stamp_ns": 1}'
            )
            is None
        )
        assert _rejections("topic") == before + 1

    def test_bad_payload_dropped(self) -> None:
        before = _rejections("payload")
        topic = "twin/robot_3/ur5/joint/elbow_joint/position"
        assert _to_point(topic, "not json") is None
        assert _to_point(topic, '{"robot_id": "robot_3", "value": "x"}') is None  # bad value type
        assert _rejections("payload") == before + 2

    def test_missing_robot_id_dropped(self) -> None:
        # A twin-services-style payload (no robot_id) no longer validates.
        before = _rejections("payload")
        assert (
            _to_point(
                "twin/robot_3/ur5/joint/elbow_joint/position", '{"value": 0.5, "stamp_ns": 42}'
            )
            is None
        )
        assert _rejections("payload") == before + 1

    def test_robot_id_mismatch_dropped(self) -> None:
        # ADR-0002: topic robot_id must equal envelope robot_id.
        before = _rejections("mismatch")
        point = _to_point(
            "twin/robot_3/ur5/joint/elbow_joint/position",
            '{"robot_id": "robot_9", "value": 1.0, "stamp_ns": 1}',
        )
        assert point is None
        assert _rejections("mismatch") == before + 1


class TestReadiness:
    def test_not_ready_before_connecting(self) -> None:
        ingestor = Ingestor(TelemetryConfig.from_env())
        assert ingestor.readiness() == {"mqtt": False, "influxdb": False}
