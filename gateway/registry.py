"""Per-node sequence, connection, and liveness state for the edge gateway."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from gateway.protocol import NodeKind, ReadingMessage, SensorMessage


class HealthStatus(StrEnum):
    ONLINE = "ONLINE"
    SUSPECT = "SUSPECT"
    OFFLINE = "OFFLINE"


class SequenceStatus(StrEnum):
    FIRST = "FIRST"
    IN_ORDER = "IN_ORDER"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    RESET = "RESET"


@dataclass(frozen=True, slots=True)
class HealthTransition:
    node_id: str
    previous: HealthStatus | None
    current: HealthStatus
    timestamp_ms: int


@dataclass(slots=True)
class NodeRecord:
    node_id: str
    node_kind: NodeKind
    connected: bool
    active_connections: int
    first_seen_ms: int
    last_seen_ms: int
    last_seen_monotonic_ns: int
    last_sequence: int
    messages_received: int = 1
    estimated_messages_missing: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    sequence_resets: int = 0
    latest_sensor_values: dict[str, float] | None = None
    health_status: HealthStatus = HealthStatus.ONLINE
    transitions: list[HealthTransition] = field(default_factory=list)


class NodeRegistry:
    """Track node observations using monotonic time for health decisions."""

    def __init__(
        self,
        *,
        expected_interval_seconds: float = 1.0,
        suspect_after_intervals: float = 3.0,
        offline_after_intervals: float = 5.0,
    ) -> None:
        if expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be positive")
        if suspect_after_intervals <= 0:
            raise ValueError("suspect_after_intervals must be positive")
        if offline_after_intervals <= suspect_after_intervals:
            raise ValueError("offline threshold must exceed suspect threshold")
        self.expected_interval_seconds = expected_interval_seconds
        self.suspect_after_intervals = suspect_after_intervals
        self.offline_after_intervals = offline_after_intervals
        self.nodes: dict[str, NodeRecord] = {}
        self.transitions: list[HealthTransition] = []

    def observe(
        self,
        message: SensorMessage,
        *,
        received_at_ms: int,
        received_monotonic_ns: int,
    ) -> SequenceStatus:
        """Update one node and return the sequence classification for this message."""

        record = self.nodes.get(message.node_id)
        if record is None:
            record = NodeRecord(
                node_id=message.node_id,
                node_kind=message.node_kind,
                connected=False,
                active_connections=0,
                first_seen_ms=received_at_ms,
                last_seen_ms=received_at_ms,
                last_seen_monotonic_ns=received_monotonic_ns,
                last_sequence=message.sequence,
                latest_sensor_values=self._sensor_values(message),
            )
            self.nodes[message.node_id] = record
            self._transition(record, HealthStatus.ONLINE, received_at_ms, initial=True)
            return SequenceStatus.FIRST

        previous_health = record.health_status
        sequence_status = self._classify_sequence(record, message.sequence, previous_health)
        record.messages_received += 1
        record.node_kind = message.node_kind
        record.last_seen_ms = received_at_ms
        record.last_seen_monotonic_ns = received_monotonic_ns
        values = self._sensor_values(message)
        if values is not None:
            record.latest_sensor_values = values
        if previous_health is not HealthStatus.ONLINE:
            self._transition(record, HealthStatus.ONLINE, received_at_ms)
        return sequence_status

    def connection_opened(self, node_id: str) -> None:
        record = self.nodes[node_id]
        record.active_connections += 1
        record.connected = True

    def connection_closed(self, node_id: str) -> None:
        record = self.nodes.get(node_id)
        if record is None:
            return
        record.active_connections = max(0, record.active_connections - 1)
        record.connected = record.active_connections > 0

    def refresh_health(
        self,
        *,
        now_monotonic_ns: int | None = None,
        now_ms: int | None = None,
    ) -> list[HealthTransition]:
        """Apply configured liveness thresholds and return transitions made now."""

        monotonic_ns = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        wall_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        changed: list[HealthTransition] = []
        suspect_ns = int(
            self.expected_interval_seconds * self.suspect_after_intervals * 1_000_000_000
        )
        offline_ns = int(
            self.expected_interval_seconds * self.offline_after_intervals * 1_000_000_000
        )
        for record in self.nodes.values():
            elapsed_ns = monotonic_ns - record.last_seen_monotonic_ns
            target = (
                HealthStatus.OFFLINE
                if elapsed_ns >= offline_ns
                else HealthStatus.SUSPECT
                if elapsed_ns >= suspect_ns
                else HealthStatus.ONLINE
            )
            if target is not record.health_status:
                changed.append(self._transition(record, target, wall_ms))
        return changed

    def _classify_sequence(
        self, record: NodeRecord, sequence: int, previous_health: HealthStatus
    ) -> SequenceStatus:
        if sequence == record.last_sequence:
            record.duplicates += 1
            return SequenceStatus.DUPLICATE
        if sequence > record.last_sequence:
            missing = sequence - record.last_sequence - 1
            record.last_sequence = sequence
            if missing:
                record.estimated_messages_missing += missing
                return SequenceStatus.GAP
            return SequenceStatus.IN_ORDER
        if previous_health is HealthStatus.OFFLINE and sequence == 0:
            record.sequence_resets += 1
            record.last_sequence = sequence
            return SequenceStatus.RESET
        record.out_of_order += 1
        return SequenceStatus.OUT_OF_ORDER

    def _transition(
        self,
        record: NodeRecord,
        current: HealthStatus,
        timestamp_ms: int,
        *,
        initial: bool = False,
    ) -> HealthTransition:
        previous = None if initial else record.health_status
        transition = HealthTransition(record.node_id, previous, current, timestamp_ms)
        record.health_status = current
        record.transitions.append(transition)
        self.transitions.append(transition)
        return transition

    @staticmethod
    def _sensor_values(message: SensorMessage) -> dict[str, float] | None:
        if not isinstance(message, ReadingMessage):
            return None
        return {
            "temperature_c": message.temperature_c,
            "humidity_pct": message.humidity_pct,
            "pressure_hpa": message.pressure_hpa,
        }
