"""Passive-discovery fleet registry (ADR-0003).

The live set is derived from the telemetry stream fleet-svc already subscribes to
(`twin/+/ur5/#`): every message refreshes its robot's last-seen time, and a robot
unseen past the liveness window is no longer live. An MQTT Last-Will ("offline")
on a robot's status topic drops it immediately — the fast path for a killed twin
that the window would otherwise take a few seconds to notice.

Gauges are reconciled on scrape, not churned on every message: at 900 msg/s per
robot, setting a per-robot gauge per message would be pure waste.
"""

from __future__ import annotations

import asyncio
import time

import aiomqtt
import structlog
from prometheus_client import Gauge

from contracts import (
    FleetSnapshot,
    RobotStatus,
    fleet_wildcard,
    robot_id_from_topic,
    status_topic,
)
from fleet_svc.config import FleetConfig

log = structlog.get_logger()

FLEET_SIZE = Gauge("twin_fleet_size", "Robots currently within the liveness window.")
ROBOT_UP = Gauge("twin_fleet_robot_up", "1 if the robot is live, else 0.", ["robot_id"])

RECONNECT_DELAY_S = 2.0
OFFLINE_PAYLOAD = b"offline"  # the Last-Will body a dying simulator leaves


class Registry:
    """Live set of twins, derived from the telemetry stream plus Last-Will."""

    def __init__(self, config: FleetConfig) -> None:
        self._config = config
        self._last_seen: dict[str, float] = {}  # robot_id -> monotonic time last seen
        self._mqtt_connected = False

    def readiness(self) -> dict[str, bool]:
        return {"mqtt": self._mqtt_connected}

    def live_robots(self) -> list[str]:
        """Sorted robot_ids seen within the liveness window."""
        cutoff = time.monotonic() - self._config.liveness_timeout_s
        return sorted(rid for rid, seen in self._last_seen.items() if seen >= cutoff)

    def snapshot(self) -> FleetSnapshot:
        """The GET /fleet body: only robots currently live. Also refreshes gauges."""
        now = time.monotonic()
        cutoff = now - self._config.liveness_timeout_s
        robots = [
            RobotStatus(robot_id=rid, last_seen_s=max(0.0, now - seen), ready=True)
            for rid, seen in sorted(self._last_seen.items())
            if seen >= cutoff
        ]
        self.refresh_metrics()
        return FleetSnapshot(robots=robots)

    def refresh_metrics(self) -> None:
        """Reconcile the fleet gauges with the current liveness state."""
        cutoff = time.monotonic() - self._config.liveness_timeout_s
        live = 0
        for rid, seen in self._last_seen.items():
            up = seen >= cutoff
            ROBOT_UP.labels(robot_id=rid).set(1 if up else 0)
            live += int(up)
        FLEET_SIZE.set(live)

    async def run(self) -> None:
        """Discover forever; reconnect with a fixed delay on broker loss."""
        while True:
            try:
                await self._discover()
            except aiomqtt.MqttError as exc:
                self._mqtt_connected = False
                log.warning("mqtt_disconnected", error=str(exc), retry_in_s=RECONNECT_DELAY_S)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _discover(self) -> None:
        cfg = self._config
        async with aiomqtt.Client(cfg.mqtt_host, cfg.mqtt_port) as mqtt:
            self._mqtt_connected = True
            await mqtt.subscribe(fleet_wildcard())  # twin/+/ur5/#
            log.info("discovering", topic=fleet_wildcard())
            async for message in mqtt.messages:
                self._observe(str(message.topic), message.payload)

    def _observe(self, topic: str, payload: object) -> None:
        try:
            robot_id = robot_id_from_topic(topic)
        except ValueError:
            return  # not a fleet topic
        if topic == status_topic(robot_id) and _is_offline(payload):
            self._drop(robot_id)
            return
        self._last_seen[robot_id] = time.monotonic()

    def _drop(self, robot_id: str) -> None:
        if self._last_seen.pop(robot_id, None) is not None:
            ROBOT_UP.labels(robot_id=robot_id).set(0)
            log.info("robot_offline", robot_id=robot_id)


def _is_offline(payload: object) -> bool:
    if isinstance(payload, bytes):
        return payload == OFFLINE_PAYLOAD
    if isinstance(payload, str):
        return payload.encode() == OFFLINE_PAYLOAD
    return False
