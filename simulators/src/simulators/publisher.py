"""One simulator's MQTT life: namespaced telemetry out, commands in, Last-Will set.

On connect it sets an MQTT Last-Will ("offline" on its status topic) so a killed
twin is seen instantly by fleet-svc (ADR-0003), publishes a birth ("online"), and
runs two tasks: a fixed-rate telemetry loop (6 joints x 3 fields per tick) and a
command loop that moves the arm's centre when a home/move_joints arrives. Broker
loss reconnects forever — a transient blip should not permanently remove a robot.
"""

from __future__ import annotations

import asyncio
import time

import aiomqtt
import structlog
from pydantic import ValidationError

from contracts import (
    UR5_JOINT_NAMES,
    CommandKind,
    JointCommand,
    JointField,
    JointTelemetry,
    command_topic,
    status_topic,
    telemetry_topic,
)
from simulators.motion import SyntheticArm, robot_phase

log = structlog.get_logger()

ONLINE = b"online"
OFFLINE = b"offline"
QOS_TELEMETRY = 0  # a lost sample is noise
QOS_STATUS = 1  # presence must arrive
RECONNECT_DELAY_S = 2.0

_FIELDS = (JointField.POSITION, JointField.VELOCITY, JointField.EFFORT)


class Simulator:
    """A single synthetic UR5 identified by robot_id."""

    def __init__(self, robot_id: str, mqtt_host: str, mqtt_port: int, hz: float) -> None:
        self._robot_id = robot_id
        self._host = mqtt_host
        self._port = mqtt_port
        self._interval = 1.0 / hz
        self._arm = SyntheticArm(phase=robot_phase(robot_id))
        self._start = time.monotonic()

    async def run(self) -> None:
        """Publish forever; reconnect with a fixed delay on broker loss."""
        while True:
            try:
                await self._session()
            except aiomqtt.MqttError as exc:
                log.warning(
                    "mqtt_disconnected",
                    robot_id=self._robot_id,
                    error=str(exc),
                    retry_in_s=RECONNECT_DELAY_S,
                )
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _session(self) -> None:
        will = aiomqtt.Will(
            topic=status_topic(self._robot_id), payload=OFFLINE, qos=QOS_STATUS, retain=True
        )
        async with aiomqtt.Client(
            self._host, self._port, will=will, identifier=self._robot_id
        ) as mqtt:
            await mqtt.publish(
                status_topic(self._robot_id), payload=ONLINE, qos=QOS_STATUS, retain=True
            )
            await mqtt.subscribe(command_topic(self._robot_id))
            log.info("robot_online", robot_id=self._robot_id, hz=round(1.0 / self._interval))
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._publish_loop(mqtt), name="telemetry")
                tg.create_task(self._command_loop(mqtt), name="commands")

    async def _publish_loop(self, mqtt: aiomqtt.Client) -> None:
        while True:
            t = time.monotonic() - self._start
            stamp_ns = time.time_ns()
            for joint in UR5_JOINT_NAMES:
                position, velocity, effort = self._arm.sample(joint, t)
                for field, value in zip(_FIELDS, (position, velocity, effort), strict=True):
                    payload = JointTelemetry(
                        robot_id=self._robot_id, value=value, stamp_ns=stamp_ns
                    )
                    await mqtt.publish(
                        telemetry_topic(self._robot_id, joint, field),
                        payload=payload.model_dump_json(),
                        qos=QOS_TELEMETRY,
                    )
            await asyncio.sleep(self._interval)

    async def _command_loop(self, mqtt: aiomqtt.Client) -> None:
        async for message in mqtt.messages:
            try:
                command = JointCommand.model_validate_json(message.payload)
            except ValidationError:
                continue
            if command.robot_id != self._robot_id:  # belt-and-suspenders (topic is ours)
                continue
            self._apply(command)

    def _apply(self, command: JointCommand) -> None:
        if command.kind is CommandKind.HOME:
            self._arm.home()
            log.info("homed", robot_id=self._robot_id)
        elif command.kind is CommandKind.MOVE_JOINTS and command.positions:
            self._arm.move(command.positions)
            log.info("moved", robot_id=self._robot_id, joints=list(command.positions))
