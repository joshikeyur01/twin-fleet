"""The MQTT half of command-svc.

Holds one long-lived broker connection; the API layer hands validated commands
to publish(). The command carries its own robot_id, so the setpoint goes to that
robot's namespaced topic (`twin/<robot_id>/ur5/cmd/joints`). When the broker is
down, publish() raises instead of buffering — an accepted command that silently
goes nowhere would be a lie, so the REST layer turns this into a 503 (each
service documents its degradation policy; ADR-0004).

Commands go out QoS 1 (at-least-once): losing a 50 Hz telemetry sample is noise,
losing a setpoint is a missing robot motion. Setpoints are idempotent, so
at-least-once is safe (this is also what makes fleet-svc's retry safe, ADR-0004).
"""

from __future__ import annotations

import asyncio

import aiomqtt
import structlog
from prometheus_client import Counter

from command_svc.config import CommandConfig
from contracts import JointCommand, command_topic

log = structlog.get_logger()

PUBLISHED = Counter("twin_commands_published_total", "Commands published to MQTT.", ["kind"])

RECONNECT_DELAY_S = 2.0
QOS_AT_LEAST_ONCE = 1


class BrokerUnavailableError(RuntimeError):
    """A command could not be published because MQTT is down."""


class Publisher:
    """Owns the broker connection and reports its readiness."""

    def __init__(self, config: CommandConfig) -> None:
        self._config = config
        self._client: aiomqtt.Client | None = None

    def readiness(self) -> dict[str, bool]:
        return {"mqtt": self._client is not None}

    async def run(self) -> None:
        """Keep one connection open forever; reconnect on loss."""
        while True:
            try:
                async with aiomqtt.Client(self._config.mqtt_host, self._config.mqtt_port) as client:
                    self._client = client
                    log.info("mqtt_connected", host=self._config.mqtt_host)
                    # No subscriptions — iterating messages is how aiomqtt
                    # surfaces a dropped connection promptly, which keeps
                    # readiness honest between publishes.
                    async for _ in client.messages:
                        pass
            except aiomqtt.MqttError as exc:
                self._client = None
                log.warning("mqtt_disconnected", error=str(exc), retry_in_s=RECONNECT_DELAY_S)
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def publish(self, command: JointCommand) -> str:
        """Publish one command to its robot's setpoint topic; return that topic."""
        client = self._client
        if client is None:
            raise BrokerUnavailableError("MQTT broker unavailable")
        topic = command_topic(command.robot_id)
        try:
            await client.publish(topic, payload=command.model_dump_json(), qos=QOS_AT_LEAST_ONCE)
        except aiomqtt.MqttError as exc:
            self._client = None
            raise BrokerUnavailableError(str(exc)) from exc
        PUBLISHED.labels(kind=command.kind.value).inc()
        return topic
