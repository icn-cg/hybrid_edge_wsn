"""Validated gateway-to-collector protocol and encoding helpers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from gateway.aggregator import WindowSummary
from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus


class UpstreamProtocolError(ValueError):
    pass


class RawUpstreamRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["raw"]
    version: Literal[1]
    record_id: str = Field(min_length=1, max_length=160)
    forwarded_at_ms: int = Field(ge=0)
    gateway_received_at_ms: int = Field(ge=0)
    gateway_received_monotonic_ns: int = Field(ge=0)
    sensor_wire_bytes: int = Field(gt=0)
    sequence_status: SequenceStatus
    reading: ReadingMessage


class AggregateUpstreamRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["aggregate"]
    version: Literal[1]
    record_id: str = Field(min_length=1, max_length=160)
    forwarded_at_ms: int = Field(ge=0)
    window_start_ms: int = Field(ge=0)
    window_end_ms: int = Field(ge=0)
    aggregation_window_seconds: float = Field(gt=0, allow_inf_nan=False)
    partial_window: bool
    reading_count: int = Field(gt=0)
    node_count: int = Field(gt=0)
    temperature_mean_c: float = Field(allow_inf_nan=False)
    temperature_min_c: float = Field(allow_inf_nan=False)
    temperature_max_c: float = Field(allow_inf_nan=False)
    humidity_mean_pct: float = Field(allow_inf_nan=False)
    humidity_min_pct: float = Field(allow_inf_nan=False)
    humidity_max_pct: float = Field(allow_inf_nan=False)
    pressure_mean_hpa: float = Field(allow_inf_nan=False)
    pressure_min_hpa: float = Field(allow_inf_nan=False)
    pressure_max_hpa: float = Field(allow_inf_nan=False)


type UpstreamRecord = Annotated[
    RawUpstreamRecord | AggregateUpstreamRecord,
    Field(discriminator="type"),
]
_UPSTREAM_ADAPTER = TypeAdapter(UpstreamRecord)


def aggregate_record(summary: WindowSummary, *, forwarded_at_ms: int) -> AggregateUpstreamRecord:
    return AggregateUpstreamRecord(
        type="aggregate",
        version=1,
        record_id=f"aggregate:{summary.window_start_ms}:{summary.window_end_ms}",
        forwarded_at_ms=forwarded_at_ms,
        **asdict(summary),
    )


def parse_upstream_record(data: bytes | str) -> UpstreamRecord:
    try:
        return _UPSTREAM_ADAPTER.validate_json(data)
    except ValidationError as exc:
        raise UpstreamProtocolError("invalid upstream record") from exc


def encode_upstream_record(record: UpstreamRecord) -> bytes:
    return record.model_dump_json().encode("utf-8") + b"\n"
