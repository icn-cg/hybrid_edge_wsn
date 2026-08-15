"""Version 1 application protocol shared by every sensor-node implementation."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

PROTOCOL_VERSION = 1
NodeKind = Literal["physical", "virtual"]
NODE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
NodeId = Annotated[str, Field(min_length=1, max_length=64, pattern=NODE_ID_PATTERN)]


class ProtocolError(ValueError):
    """Raised when a wire message is not valid protocol data."""

    def __init__(self, reason: Literal["malformed_json", "schema_validation"]) -> None:
        super().__init__(f"invalid sensor message: {reason}")
        self.reason = reason


class BaseSensorMessage(BaseModel):
    """Fields present in every sensor-to-gateway message."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    node_id: NodeId
    node_kind: NodeKind
    sequence: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)

    @field_validator("version", mode="before")
    @classmethod
    def version_must_be_json_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


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
_NODE_ID_ADAPTER = TypeAdapter(NodeId)


def parse_message(data: bytes | str) -> SensorMessage:
    """Parse and validate one JSON value (without its NDJSON delimiter)."""

    try:
        return _MESSAGE_ADAPTER.validate_json(data)
    except ValidationError as exc:
        reason = (
            "malformed_json"
            if any(error["type"] == "json_invalid" for error in exc.errors())
            else "schema_validation"
        )
        raise ProtocolError(reason) from exc


def validate_node_id(node_id: str) -> str:
    """Validate a node ID before a node starts its asynchronous send loop."""

    try:
        return cast(str, _NODE_ID_ADAPTER.validate_python(node_id, strict=True))
    except ValidationError as exc:
        raise ValueError("node_id does not satisfy the protocol") from exc


def encode_message(message: SensorMessage) -> bytes:
    """Encode a validated message as one compact NDJSON record."""

    return message.model_dump_json().encode("utf-8") + b"\n"
