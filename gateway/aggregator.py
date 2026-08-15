"""Pure time-window aggregation for validated environmental readings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gateway.protocol import ReadingMessage
from gateway.registry import SequenceStatus

if TYPE_CHECKING:
    from gateway.server import ReceivedMessage


@dataclass(frozen=True, slots=True)
class WindowSummary:
    window_start_ms: int
    window_end_ms: int
    aggregation_window_seconds: float
    partial_window: bool
    reading_count: int
    node_count: int
    temperature_mean_c: float
    temperature_min_c: float
    temperature_max_c: float
    humidity_mean_pct: float
    humidity_min_pct: float
    humidity_max_pct: float
    pressure_mean_hpa: float
    pressure_min_hpa: float
    pressure_max_hpa: float


@dataclass(slots=True)
class _Accumulator:
    start_ms: int
    start_monotonic_ns: int
    deadline_monotonic_ns: int
    nodes: set[str] = field(default_factory=set)
    count: int = 0
    temperature_sum: float = 0.0
    temperature_min: float = float("inf")
    temperature_max: float = float("-inf")
    humidity_sum: float = 0.0
    humidity_min: float = float("inf")
    humidity_max: float = float("-inf")
    pressure_sum: float = 0.0
    pressure_min: float = float("inf")
    pressure_max: float = float("-inf")


class WindowAggregator:
    """Aggregate readings into windows anchored by the first gateway receive time."""

    def __init__(self, window_seconds: float) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds
        self.window_ns = int(window_seconds * 1_000_000_000)
        self._current: _Accumulator | None = None

    @property
    def has_data(self) -> bool:
        return self._current is not None and self._current.count > 0

    @property
    def deadline_monotonic_ns(self) -> int | None:
        return None if self._current is None else self._current.deadline_monotonic_ns

    def add(self, received: ReceivedMessage) -> WindowSummary | None:
        """Add one reading and return a completed prior window if the boundary was crossed."""

        if not isinstance(received.message, ReadingMessage):
            return None
        if received.sequence_status is SequenceStatus.DUPLICATE:
            return None
        completed: WindowSummary | None = None
        if self._current is None:
            self._current = self._new_accumulator(received)
        elif received.received_monotonic_ns >= self._current.deadline_monotonic_ns:
            completed = self._summarize(partial_window=False)
            self._current = self._new_accumulator(received)
        self._accumulate(received.message)
        return completed

    def flush(
        self, *, partial_window: bool, window_end_ms: int | None = None
    ) -> WindowSummary | None:
        """Return and clear the current non-empty window."""

        if not self.has_data:
            self._current = None
            return None
        summary = self._summarize(
            partial_window=partial_window, window_end_ms=window_end_ms
        )
        self._current = None
        return summary

    def _new_accumulator(self, received: ReceivedMessage) -> _Accumulator:
        return _Accumulator(
            start_ms=received.received_at_ms,
            start_monotonic_ns=received.received_monotonic_ns,
            deadline_monotonic_ns=received.received_monotonic_ns + self.window_ns,
        )

    def _accumulate(self, reading: ReadingMessage) -> None:
        assert self._current is not None
        current = self._current
        current.nodes.add(reading.node_id)
        current.count += 1
        current.temperature_sum += reading.temperature_c
        current.temperature_min = min(current.temperature_min, reading.temperature_c)
        current.temperature_max = max(current.temperature_max, reading.temperature_c)
        current.humidity_sum += reading.humidity_pct
        current.humidity_min = min(current.humidity_min, reading.humidity_pct)
        current.humidity_max = max(current.humidity_max, reading.humidity_pct)
        current.pressure_sum += reading.pressure_hpa
        current.pressure_min = min(current.pressure_min, reading.pressure_hpa)
        current.pressure_max = max(current.pressure_max, reading.pressure_hpa)

    def _summarize(
        self, *, partial_window: bool, window_end_ms: int | None = None
    ) -> WindowSummary:
        assert self._current is not None and self._current.count > 0
        current = self._current
        planned_end_ms = current.start_ms + round(self.window_seconds * 1_000)
        return WindowSummary(
            window_start_ms=current.start_ms,
            window_end_ms=planned_end_ms if window_end_ms is None else window_end_ms,
            aggregation_window_seconds=self.window_seconds,
            partial_window=partial_window,
            reading_count=current.count,
            node_count=len(current.nodes),
            temperature_mean_c=current.temperature_sum / current.count,
            temperature_min_c=current.temperature_min,
            temperature_max_c=current.temperature_max,
            humidity_mean_pct=current.humidity_sum / current.count,
            humidity_min_pct=current.humidity_min,
            humidity_max_pct=current.humidity_max,
            pressure_mean_hpa=current.pressure_sum / current.count,
            pressure_min_hpa=current.pressure_min,
            pressure_max_hpa=current.pressure_max,
        )
