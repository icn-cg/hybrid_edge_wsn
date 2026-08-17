"""Validated Phase 3 experiment configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    experiment_type: Literal["scaling", "aggregation", "failure", "impairment"]
    node_count: int = Field(ge=1, le=10_000)
    physical_node_count: int = Field(default=0, ge=0)
    virtual_node_count: int = Field(ge=1)
    duration_seconds: float = Field(gt=0)
    warmup_seconds: float = Field(default=0.0, ge=0)
    sampling_interval_ms: float = Field(default=1_000.0, gt=0)
    aggregation_mode: Literal["raw", "aggregated"] = "raw"
    aggregation_window_seconds: float = Field(default=0.0, ge=0)
    impairment_mode: Literal["none", "application"] = "none"
    drop_probability: float = Field(default=0.0, ge=0, le=1)
    artificial_delay_ms: float = Field(default=0.0, ge=0)
    random_seed: int = 662
    gateway_host: str = "127.0.0.1"
    gateway_port: int = Field(default=0, ge=0, le=65_535)
    collector_host: str = "127.0.0.1"
    collector_port: int = Field(default=0, ge=0, le=65_535)
    expected_interval_seconds: float = Field(gt=0)
    suspect_after_intervals: float = Field(default=3.0, gt=0)
    offline_after_intervals: float = Field(default=5.0, gt=0)
    liveness_check_interval_seconds: float = Field(default=0.5, gt=0)
    forwarder_queue_size: int = Field(default=10_000, ge=1)
    storage_queue_size: int = Field(default=10_000, ge=1)
    event_queue_size: int = Field(default=10_000, ge=1)
    metrics_interval_seconds: float = Field(default=0.5, gt=0)
    failure_at_seconds: float | None = None
    recovery_at_seconds: float | None = None
    campaign_id: str | None = None
    condition_id: str | None = None
    repetition_index: int = Field(default=0, ge=0)
    base_seed: int = 662
    run_classification: Literal["engineering_rehearsal", "controlled_final"] = (
        "engineering_rehearsal"
    )

    @model_validator(mode="after")
    def validate_relationships(self) -> ExperimentConfig:
        if self.node_count != self.physical_node_count + self.virtual_node_count:
            raise ValueError("node_count must equal physical plus virtual nodes")
        if self.offline_after_intervals <= self.suspect_after_intervals:
            raise ValueError("offline threshold must exceed suspect threshold")
        if self.aggregation_mode == "raw" and self.aggregation_window_seconds != 0:
            raise ValueError("raw mode requires a zero aggregation window")
        if self.aggregation_mode == "aggregated" and self.aggregation_window_seconds <= 0:
            raise ValueError("aggregated mode requires a positive window")
        if self.impairment_mode == "none" and (
            self.drop_probability > 0 or self.artificial_delay_ms > 0
        ):
            raise ValueError("application impairment values require impairment_mode=application")
        if self.failure_at_seconds is not None and not (
            0 <= self.failure_at_seconds < self.duration_seconds
        ):
            raise ValueError("failure time must be inside measurement interval")
        if self.recovery_at_seconds is not None and (
            self.failure_at_seconds is None
            or self.recovery_at_seconds <= self.failure_at_seconds
            or self.recovery_at_seconds >= self.duration_seconds
        ):
            raise ValueError("recovery must follow failure inside measurement interval")
        if (self.campaign_id is None) != (self.condition_id is None):
            raise ValueError("campaign_id and condition_id must be supplied together")
        if self.campaign_id is not None and self.random_seed != (
            self.base_seed + self.repetition_index
        ):
            raise ValueError("campaign random_seed must equal base_seed + repetition_index")
        if self.run_classification == "controlled_final" and self.campaign_id is None:
            raise ValueError("controlled final runs require campaign and condition identifiers")
        return self
