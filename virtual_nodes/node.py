"""A deterministic synthetic node using the same protocol as physical nodes."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import socket
import time
from dataclasses import dataclass

from gateway.protocol import PROTOCOL_VERSION, ReadingMessage, encode_message, validate_node_id

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VirtualNodeConfig:
    node_id: str
    host: str = "127.0.0.1"
    port: int = 8662
    sampling_interval: float = 1.0
    startup_delay: float = 0.0
    drop_probability: float = 0.0
    artificial_delay: float = 0.0
    seed: int = 662
    reconnect_initial: float = 0.1
    reconnect_max: float = 5.0

    def __post_init__(self) -> None:
        validate_node_id(self.node_id)
        if self.sampling_interval <= 0:
            raise ValueError("sampling_interval must be positive")
        if self.startup_delay < 0 or self.artificial_delay < 0:
            raise ValueError("delays must be non-negative")
        if not 0.0 <= self.drop_probability <= 1.0:
            raise ValueError("drop_probability must be between 0 and 1")
        if self.reconnect_initial <= 0 or self.reconnect_max < self.reconnect_initial:
            raise ValueError("invalid reconnect backoff")


class VirtualNode:
    """Generate synthetic environmental data and reconnect when TCP fails."""

    def __init__(self, config: VirtualNodeConfig) -> None:
        self.config = config
        self.sequence = 0
        self.samples_generated = 0
        self.send_attempts = 0
        self.messages_sent = 0
        self.application_drops = 0
        self._random = random.Random(config.seed)
        self._started_monotonic: float | None = None

    def make_reading(self) -> ReadingMessage:
        """Create a labeled synthetic reading with slow drift and bounded noise."""

        if self._started_monotonic is None:
            self._started_monotonic = time.monotonic()
        elapsed = time.monotonic() - self._started_monotonic
        slow_drift = math.sin(elapsed / 60.0)
        reading = ReadingMessage(
            type="reading",
            version=PROTOCOL_VERSION,
            node_id=self.config.node_id,
            node_kind="virtual",
            sequence=self.sequence,
            timestamp_ms=time.time_ns() // 1_000_000,
            temperature_c=round(22.0 + 0.8 * slow_drift + self._random.gauss(0, 0.08), 3),
            humidity_pct=round(48.0 + 1.5 * slow_drift + self._random.gauss(0, 0.2), 3),
            pressure_hpa=round(1013.0 + 0.6 * slow_drift + self._random.gauss(0, 0.08), 3),
        )
        self.sequence += 1
        self.samples_generated += 1
        return reading

    async def run(
        self,
        *,
        max_samples: int | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run until stopped, reconnecting with capped exponential backoff."""

        if max_samples is not None and max_samples < 0:
            raise ValueError("max_samples must be non-negative")
        if max_samples == 0:
            return
        if self.config.startup_delay:
            await asyncio.sleep(self.config.startup_delay)

        backoff = self.config.reconnect_initial
        while not self._done(max_samples, stop_event):
            writer: asyncio.StreamWriter | None = None
            try:
                _reader, writer = await asyncio.open_connection(
                    self.config.host, self.config.port
                )
                raw_socket: socket.socket | None = writer.get_extra_info("socket")
                if raw_socket is not None:
                    raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                LOGGER.debug("%s connected", self.config.node_id)
                backoff = self.config.reconnect_initial
                await self._send_loop(writer, max_samples, stop_event)
            except (ConnectionError, OSError, TimeoutError):
                if not self._done(max_samples, stop_event):
                    LOGGER.debug("%s reconnecting in %.2fs", self.config.node_id, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.config.reconnect_max)
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except ConnectionError:
                        pass

    def _done(self, max_samples: int | None, stop_event: asyncio.Event | None) -> bool:
        return (max_samples is not None and self.samples_generated >= max_samples) or (
            stop_event is not None and stop_event.is_set()
        )

    async def _send_loop(
        self,
        writer: asyncio.StreamWriter,
        max_samples: int | None,
        stop_event: asyncio.Event | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        next_sample = loop.time()
        while not self._done(max_samples, stop_event):
            reading = self.make_reading()
            if self._random.random() >= self.config.drop_probability:
                self.send_attempts += 1
                if self.config.artificial_delay:
                    await asyncio.sleep(self.config.artificial_delay)
                writer.write(encode_message(reading))
                await writer.drain()
                self.messages_sent += 1
            else:
                self.application_drops += 1

            next_sample += self.config.sampling_interval
            await asyncio.sleep(max(0.0, next_sample - loop.time()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one synthetic WSN sensor node")
    parser.add_argument("--node-id", default="virtual-001")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8662)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--samples", type=int)
    parser.add_argument("--seed", type=int, default=662)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    node = VirtualNode(
        VirtualNodeConfig(
            node_id=args.node_id,
            host=args.host,
            port=args.port,
            sampling_interval=args.interval,
            seed=args.seed,
        )
    )
    asyncio.run(node.run(max_samples=args.samples))


if __name__ == "__main__":
    main()
