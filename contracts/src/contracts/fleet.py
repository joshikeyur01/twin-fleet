"""Fleet coordination contracts (ADR-0004).

The shapes for ``fleet-svc``'s coordinated command: the fleet-wide request
(``FleetCommand``, no ``robot_id`` — it targets every live twin) and the
aggregated per-robot outcome (``FleetCommandResult``). Best-effort fan-out with
per-robot accounting: a dead twin is a failed entry, never a silent drop. The
count fields are derived from ``results`` so the body can never disagree with
itself.

``fleet-svc`` turns one ``FleetCommand`` into one ``JointCommand`` per live robot
(stamping each ``robot_id``) and issues them through command-svc; that expansion
lives in the service, not here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, model_validator

from contracts.models import ROBOT_ID_PATTERN, SCHEMA_VERSION, CommandKind


class FleetCommandStatus(StrEnum):
    """Per-robot outcome of a coordinated command."""

    OK = "ok"
    FAILED = "failed"


class FleetCommand(BaseModel):
    """A command addressed to the whole fleet. Same kind/positions semantics as a
    single ``JointCommand`` minus ``robot_id`` — fleet-svc fans it out to every
    live robot."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    kind: CommandKind
    positions: dict[str, float] | None = Field(
        default=None, description="Target angle in radians, keyed by joint name."
    )
    duration_s: float = Field(default=2.0, gt=0, le=30)

    @model_validator(mode="after")
    def _positions_match_kind(self) -> FleetCommand:
        if self.kind is CommandKind.MOVE_JOINTS and not self.positions:
            raise ValueError("move_joints requires positions")
        if self.kind is CommandKind.HOME and self.positions:
            raise ValueError("home takes no positions")
        return self


class RobotCommandResult(BaseModel):
    """One robot's outcome within a fan-out. ``command_id`` is present on success
    (command-svc's receipt), ``detail`` carries the reason on failure."""

    robot_id: str = Field(..., pattern=ROBOT_ID_PATTERN)
    status: FleetCommandStatus
    detail: str | None = None
    command_id: str | None = None


class FleetCommandResult(BaseModel):
    """Aggregated fan-out outcome — the ``fleet-svc`` response body. Counts are
    derived from ``results`` (ADR-0004: the body cannot contradict itself)."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    kind: CommandKind
    results: list[RobotCommandResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return len(self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> int:
        return sum(r.status is FleetCommandStatus.OK for r in self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed(self) -> int:
        return sum(r.status is FleetCommandStatus.FAILED for r in self.results)

    @property
    def all_ok(self) -> bool:
        """True when every robot succeeded — fleet-svc returns 200, else 207."""
        return all(r.status is FleetCommandStatus.OK for r in self.results)


class RobotStatus(BaseModel):
    """One robot's line in the registry (ADR-0003): identity, how long since its
    last telemetry, and whether that is within the liveness window."""

    robot_id: str = Field(..., pattern=ROBOT_ID_PATTERN)
    last_seen_s: float = Field(..., ge=0, description="Seconds since last telemetry.")
    ready: bool


class FleetSnapshot(BaseModel):
    """The GET /fleet response: every robot fleet-svc currently considers live.
    ``size`` is derived from ``robots`` so it cannot disagree."""

    robots: list[RobotStatus]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size(self) -> int:
        return len(self.robots)
