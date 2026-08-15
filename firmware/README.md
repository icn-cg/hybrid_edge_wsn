# Physical node firmware preparation

This directory contains the Phase 4 preparation for one ESP32 plus BME280 node. The pure protocol,
validation, sequence, and reconnect-backoff logic is host tested. The Arduino entry point is
implemented but is not yet board-built, wired, flashed, or hardware validated because the exact
ESP32 board and BME280 breakout have not been identified.

## Existing version-1 wire contract

The firmware uses the existing Python gateway contract without physical-node exceptions. One record
is one compact JSON object followed by exactly `\n` and no TCP debug text.

| Field | Required JSON type and constraint |
|---|---|
| `type` | string, exactly `"reading"` |
| `version` | integer, exactly `1` |
| `node_id` | 1–64 characters; starts alphanumeric; remainder alphanumeric, `_`, `.`, or `-` |
| `node_kind` | string, exactly `"physical"` |
| `sequence` | non-negative integer |
| `timestamp_ms` | non-negative integer |
| `temperature_c` | finite number from −100 through 100 |
| `humidity_pct` | finite number from 0 through 100 |
| `pressure_hpa` | finite number greater than 0 through 1200 |

Example record produced for the initial configuration (shown across one line as transmitted):

```json
{"type":"reading","version":1,"node_id":"physical-001","node_kind":"physical","sequence":0,"timestamp_ms":1234,"temperature_c":23.820,"humidity_pct":47.310,"pressure_hpa":1012.400}
```

The actual wire value has one trailing newline. It is under the firmware's 512-byte bound and the
gateway's 64 KiB limit.

## Architecture and behavior

- `lib/NodeProtocol/` is standard C++17 with no Arduino dependencies. It validates configuration and
  readings, encodes exact NDJSON, owns sequence state, and calculates capped exponential backoff.
- `src/main.cpp` is a small cooperative Arduino loop. It services sensor initialization, Wi-Fi,
  persistent TCP reconnect, sampling, and writes without a task framework or unbounded queue.
- The [Adafruit BME280 Library 2.3.x](https://registry.platformio.org/libraries/adafruit/Adafruit%20BME280%20Library)
  is selected from the PlatformIO registry. Its
  [public header](https://github.com/adafruit/Adafruit_BME280_Library/blob/master/Adafruit_BME280.h)
  exposes `begin(address, Wire)` and defines both normal BME280 I2C addresses, `0x76` and `0x77`.
  The firmware tries both and never fabricates readings.
- Wi-Fi and TCP retries use 1-second initial exponential backoff capped at 30 seconds. A TCP connect
  attempt has a 3-second bound. Connections are persistent; there is no per-reading reconnect.
- The BME280 is sampled at the configured interval whenever it is ready, including sensor-only
  bring-up. Samples taken without TCP are shown on Serial and discarded without consuming sequence;
  there is no offline queue or later burst.
- Sequence starts at `0` on each boot and advances only after a complete local TCP write. A failed or
  partial write closes the socket and reuses the sequence after reconnect. TCP has no application
  acknowledgment, so a rare connection loss after local buffering can still produce a duplicate or
  gap; the gateway records both conditions.
- `timestamp_ms` is monotonic uptime in milliseconds from `esp_timer_get_time()`, not Unix time.
  This satisfies the current non-negative-integer schema without pretending clocks are synchronized.
  It must not be used for physical one-way latency.

## Configuration and secrets

Non-secret settings live in `include/config.hpp`: node ID, gateway host, gateway port, sample
interval, timeouts, and backoff. The rehearsal Pi address is present in this one file only and must
be checked after DHCP changes.

Sensor-only firmware can build without a secrets file; networking stays disabled. Before Stage B,
create the ignored credentials file:

```bash
cp firmware/include/secrets.example.hpp firmware/include/secrets.hpp
```

Replace both placeholders locally. Never commit `secrets.hpp`; firmware diagnostics never print its
contents.

## Host-testable logic

PlatformIO is not installed in the current Mac environment, so the portable tests can be compiled
directly with the system C++ compiler:

```bash
firmware/scripts/run_host_tests.sh
```

Once PlatformIO is installed, the configured native environment can run the same custom tests:

```bash
pio test -d firmware -e native
```

These tests validate encoding, newline framing, schema ranges, configuration, boot sequence, write
success semantics, and capped backoff. They do not claim to test Wi-Fi, I2C, or hardware.

## Board identification required before build or wiring

Do not uncomment or invent an ESP32 environment in `platformio.ini` yet. First record:

1. Exact ESP32 board product/model and its PlatformIO board ID.
2. Exact BME280 breakout product/model, pin labels, and acceptable supply voltage.
3. Board-specific default or selected SDA and SCL GPIO pins.
4. USB serial/upload port and whether a driver or manual boot-button sequence is required.

After those are confirmed, add the commented Arduino environment from `platformio.ini` with the
real board ID. The expected command forms will then be:

```bash
pio run -d firmware -e physical-node
pio run -d firmware -e physical-node -t upload --upload-port <confirmed-port>
pio device monitor -d firmware --port <confirmed-port> --baud 115200
```

The exact upload port and wiring cannot be truthfully supplied until the two modules are identified.
