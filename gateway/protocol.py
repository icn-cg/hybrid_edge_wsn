"""Version 1 application protocol shared by every sensor-node implementation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

PROTOCOL_VERSION = 1
NodeKind = Literal["physical", "virtual"]


class ProtocolError(ValueError):
    """Raised when a wire message is not valid protocol data."""


class BaseSensorMessage(BaseModel):
    """Fields present in every sensor-to-gateway message."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    node_kind: NodeKind
    sequence: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)


class ReadingMessage(BaseSensorMessage):
    """One environmental sample."""

    type: Literal["reading"]
    temperature_c: float = Field(ge=-100.0, le=100.0, allow_inf_nan=False)
    humidity_pct: float = Field(ge=0.0, le=100.0, allow_inf_nan=False)
    pressure_hpa: float = Field(gt=0.0, le=1200.0, allow_inf_nan=False)


class HeartbeatMessage(BaseSensorMessage):
    """A liveness message without a sensor sample."""

    type: Literal["heartbeat"]


type SensorMessage = Annotated[
    ReadingMessage | HeartbeatMessage,
    Field(discriminator="type"),
]
_MESSAGE_ADAPTER = TypeAdapter(SensorMessage)


def parse_message(data: bytes | str) -> SensorMessage:
    """Parse and validate one JSON value (without its NDJSON delimiter)."""

    try:
        return _MESSAGE_ADAPTER.validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise ProtocolError("invalid sensor message") from exc


def encode_message(message: SensorMessage) -> bytes:
    """Encode a validated message as one compact NDJSON record."""

    return message.model_dump_json().encode("utf-8") + b"\n"
