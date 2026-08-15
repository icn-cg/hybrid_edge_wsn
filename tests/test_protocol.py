import json

import pytest

from gateway.protocol import (
    HeartbeatMessage,
    ProtocolError,
    ReadingMessage,
    encode_message,
    parse_message,
)


def reading_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "reading",
        "version": 1,
        "node_id": "virtual-001",
        "node_kind": "virtual",
        "sequence": 7,
        "timestamp_ms": 1_786_482_001_123,
        "temperature_c": 23.82,
        "humidity_pct": 47.31,
        "pressure_hpa": 1012.4,
    }
    payload.update(overrides)
    return payload


def parse_payload(payload: dict[str, object]):
    return parse_message(json.dumps(payload))


def test_valid_reading_is_accepted() -> None:
    message = parse_payload(reading_payload())

    assert isinstance(message, ReadingMessage)
    assert message.node_id == "virtual-001"
    assert message.temperature_c == pytest.approx(23.82)


def test_valid_heartbeat_is_accepted() -> None:
    message = parse_payload(
        {
            "type": "heartbeat",
            "version": 1,
            "node_id": "esp32-01",
            "node_kind": "physical",
            "sequence": 8,
            "timestamp_ms": 1_786_482_002_123,
        }
    )

    assert isinstance(message, HeartbeatMessage)
    assert message.node_kind == "physical"


@pytest.mark.parametrize(
    "wire_value",
    [
        "not-json",
        json.dumps(reading_payload(node_id=None)),
        json.dumps(reading_payload(version=2)),
        json.dumps(reading_payload(temperature_c=float("nan"))),
        json.dumps(reading_payload(humidity_pct=101.0)),
        json.dumps(reading_payload(sequence=-1)),
        json.dumps(reading_payload(unexpected="field")),
    ],
    ids=[
        "malformed-json",
        "missing-node-id",
        "unsupported-version",
        "non-finite-number",
        "out-of-range-number",
        "negative-sequence",
        "unexpected-field",
    ],
)
def test_invalid_messages_are_rejected(wire_value: str) -> None:
    with pytest.raises(ProtocolError):
        parse_message(wire_value)


def test_missing_node_id_is_rejected() -> None:
    payload = reading_payload()
    del payload["node_id"]

    with pytest.raises(ProtocolError):
        parse_payload(payload)


def test_encode_message_adds_exactly_one_ndjson_delimiter() -> None:
    message = parse_payload(reading_payload())
    encoded = encode_message(message)

    assert encoded.endswith(b"\n")
    assert not encoded.endswith(b"\n\n")
    assert parse_message(encoded.rstrip(b"\n")) == message
