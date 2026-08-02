"""MQTT → contract validation → InfluxDB, for the whole fleet.

The loop is the service: subscribe once to the telemetry wildcard
(`twin/+/ur5/joint/+/+`), validate every payload against contracts, cross-check
the topic's robot_id against the envelope's (ADR-0002), and write points tagged
by robot_id. Invalid input is counted and dropped — never written, never fatal.
Broker loss flips readiness and retries forever; recovery needs no manual step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiomqtt
import structlog
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.write.point import Point
from influxdb_client.domain.write_precision import WritePrecision
from prometheus_client import Counter
from pydantic import ValidationError

from contracts import JointTelemetry, parse_telemetry_topic, telemetry_wildcard
from telemetry_svc.config import TelemetryConfig

log = structlog.get_logger()

MESSAGES = Counter("twin_telemetry_messages_total", "Telemetry messages received from MQTT.")
REJECTED = Counter(
    "twin_telemetry_rejected_total",
    "Messages dropped before writing.",
    ["reason"],  # "topic" | "payload" | "mismatch"
)
POINTS_WRITTEN = Counter(
    "twin_influx_points_written_total", "Telemetry points successfully written to InfluxDB."
)
WRITE_FAILURES = Counter("twin_influx_write_failures_total", "InfluxDB writes that raised.")

RECONNECT_DELAY_S = 2.0

# Batched writes (ADR-0005 fix): per-point synchronous writes capped ingest at
# ~700 points/s (one HTTP round-trip each). Accumulate points and flush as one
# Influx call when the buffer fills OR the interval elapses — the latter keeps
# low-rate telemetry landing promptly even when the buffer never fills.
BATCH_MAX_POINTS = 500
BATCH_MAX_INTERVAL_S = 0.5


class Ingestor:
    """Owns the MQTT→InfluxDB loop and reports its readiness."""

    def __init__(self, config: TelemetryConfig) -> None:
        self._config = config
        self._mqtt_connected = False
        self._influx_ok = False

    def readiness(self) -> dict[str, bool]:
        return {"mqtt": self._mqtt_connected, "influxdb": self._influx_ok}

    async def run(self) -> None:
        """Consume telemetry forever; reconnect with a fixed delay on broker loss."""
        while True:
            try:
                await self._consume()
            except aiomqtt.MqttError as exc:
                self._mqtt_connected = False
                log.warning("mqtt_disconnected", error=str(exc), retry_in_s=RECONNECT_DELAY_S)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _consume(self) -> None:
        cfg = self._config
        async with (
            InfluxDBClientAsync(
                url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org
            ) as influx,
            aiomqtt.Client(cfg.mqtt_host, cfg.mqtt_port) as mqtt,
        ):
            self._mqtt_connected = True
            self._influx_ok = await influx.ping()
            write_api = influx.write_api()
            topic_filter = telemetry_wildcard()  # twin/+/ur5/joint/+/+ — every robot
            await mqtt.subscribe(topic_filter)
            log.info("consuming", topic=topic_filter, influx=cfg.influx_url, batch=BATCH_MAX_POINTS)

            buffer: list[Point] = []
            messages = aiter(mqtt.messages)
            while True:
                try:
                    message = await asyncio.wait_for(anext(messages), BATCH_MAX_INTERVAL_S)
                except TimeoutError:
                    await self._flush(write_api, buffer)  # idle: land what we have
                    continue
                except StopAsyncIteration:
                    break
                MESSAGES.inc()
                raw = message.payload
                if not isinstance(raw, bytes | str):
                    REJECTED.labels(reason="payload").inc()
                    continue
                point = _to_point(str(message.topic), raw)
                if point is not None:
                    buffer.append(point)
                if len(buffer) >= BATCH_MAX_POINTS:
                    await self._flush(write_api, buffer)  # buffer full: one batch write

    async def _flush(self, write_api: Any, buffer: list[Point]) -> None:
        """Write buffered points as one Influx call. On failure the batch is
        dropped (bounded loss keeps memory flat — a lost batch is like lost QoS-0
        samples, noise); a write must never kill ingest."""
        if not buffer:
            return
        batch = buffer[:]
        buffer.clear()
        try:
            await write_api.write(bucket=self._config.influx_bucket, record=batch)
            self._influx_ok = True
            POINTS_WRITTEN.inc(len(batch))
        except Exception as exc:
            WRITE_FAILURES.inc()
            self._influx_ok = False
            log.warning("influx_write_failed", error=str(exc), dropped=len(batch))


def _to_point(topic: str, payload: bytes | str) -> Point | None:
    """One validated telemetry message becomes one point; anything else, None.

    The robot_id in the topic and the robot_id in the envelope must agree — a
    mismatch is a misrouted or spoofed message and is dropped, not written."""
    try:
        robot_id, joint, field = parse_telemetry_topic(topic)
    except ValueError:
        REJECTED.labels(reason="topic").inc()
        return None
    try:
        sample = JointTelemetry.model_validate_json(payload)
    except ValidationError:
        REJECTED.labels(reason="payload").inc()
        return None
    if sample.robot_id != robot_id:
        REJECTED.labels(reason="mismatch").inc()
        log.warning("robot_id_mismatch", topic_robot_id=robot_id, payload_robot_id=sample.robot_id)
        return None
    point: Point = (
        Point("joint_telemetry")
        .tag("robot_id", robot_id)
        .tag("joint", joint)
        .tag("metric", field.value)
        .field("value", sample.value)
        .time(sample.stamp_ns, WritePrecision.NS)
    )
    return point
