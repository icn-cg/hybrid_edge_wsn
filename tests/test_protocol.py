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


def test_json_integer_sensor_values_are_accepted() -> None:
    """ESP32 JSON encoders often emit whole numbers without a decimal point."""

    message = parse_payload(
        reading_payload(temperature_c=23, humidity_pct=47, pressure_hpa=1013)
    )

    assert isinstance(message, ReadingMessage)
    assert message.temperature_c == 23.0
    assert message.humidity_pct == 47.0
    assert message.pressure_hpa == 1013.0


def test_trailing_cr_is_accepted() -> None:
    """println()-style CRLF leaves a CR after the gateway strips the newline."""

    message = parse_message(json.dumps(reading_payload()) + "\r")

    assert isinstance(message, ReadingMessage)
    assert message.node_id == "virtual-001"


@pytest.mark.parametrize(
    "node_id",
    ["", "virtual 001", "a" * 65, "-leading-hyphen"],
    ids=["empty", "space", "too-long", "leading-hyphen"],
)
def test_invalid_node_id_is_rejected(node_id: str) -> None:
    with pytest.raises(ProtocolError):
        parse_payload(reading_payload(node_id=node_id))


def test_infinity_is_rejected() -> None:
    wire_value = (
        '{"type":"reading","version":1,"node_id":"virtual-001","node_kind":"virtual",'
        '"sequence":7,"timestamp_ms":1,"temperature_c":Infinity,"humidity_pct":47.31,'
        '"pressure_hpa":1012.4}'
    )

    with pytest.raises(ProtocolError):
        parse_message(wire_value)


def test_boolean_sequence_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        parse_payload(reading_payload(sequence=True))


@pytest.mark.parametrize("version", [True, 1.0], ids=["boolean", "float"])
def test_protocol_version_requires_json_integer(version: object) -> None:
    with pytest.raises(ProtocolError):
        parse_payload(reading_payload(version=version))
