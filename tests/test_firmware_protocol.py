import subprocess
from pathlib import Path

from gateway.protocol import ReadingMessage, parse_message

REPO = Path(__file__).resolve().parents[1]
HOST_TEST = REPO / "firmware" / "scripts" / "run_host_tests.sh"


def test_firmware_emitter_parses_with_unchanged_python_schema() -> None:
    completed = subprocess.run(
        [str(HOST_TEST), "--emit-example"],
        check=True,
        capture_output=True,
    )
    payload = completed.stdout

    assert payload.startswith(b"{")
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert len(payload) < 512

    message = parse_message(payload[:-1])
    assert isinstance(message, ReadingMessage)
    assert message.type == "reading"
    assert message.version == 1
    assert message.node_id == "physical-001"
    assert message.node_kind == "physical"
    assert message.sequence == 0
    assert message.timestamp_ms == 1234
    assert message.temperature_c == 23.82
    assert message.humidity_pct == 47.31
    assert message.pressure_hpa == 1012.4
