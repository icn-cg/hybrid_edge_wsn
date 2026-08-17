# Physical node firmware bring-up

This directory contains the Phase 4 firmware for one ESP32 plus BME280 node. The identified board is
a classic ESP32 DevKit V1 with a CP2102 USB-to-UART bridge. The PlatformIO environment therefore uses
PlatformIO's documented
[`esp32doit-devkit-v1`](https://docs.platformio.org/en/latest/boards/espressif32/esp32doit-devkit-v1.html)
board definition, the Arduino framework, and C++17. USB upload, Serial boot, Wi-Fi association, and
DHCP have passed on ESP32-B, ESP32-C, and ESP32-D. A pre-soldered BME280 on ESP32-B has passed local
I2C/Serial validation and physical RAW delivery through the Pi to the Mac collector. The
reproducible target environment pins Espressif 32 platform `7.0.1`, Arduino-ESP32
framework package `3.20017.241212`, and Adafruit BME280 library `2.3.0`.

All physical devices used or evaluated during bring-up—including compute hosts, access points,
ESP32 boards, BME280 breakouts, and USB support hardware—are tracked in
[HARDWARE_INVENTORY.md](HARDWARE_INVENTORY.md).

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
- The [Adafruit BME280 Library 2.3.0](https://registry.platformio.org/libraries/adafruit/Adafruit%20BME280%20Library)
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
- I2C is explicitly initialized with SDA on GPIO 21 and SCL on GPIO 22. The firmware logs those pins,
  then tries BME280 addresses `0x76` and `0x77` in that order.
- A missing BME280 is not fatal. The firmware reports the absence and retries every five seconds.
  With configured credentials, Wi-Fi and the gateway TCP connection are still serviced, but the
  sampling guard emits no reading until a real BME280 is detected and returns valid values. A
  sensor-free TCP connection may be closed by the gateway's idle timeout and safely reconnected.

## Configuration and secrets

Non-secret settings live in `include/config.hpp`: the default node profile, gateway host, gateway
port, sample interval, timeouts, and backoff. PlatformIO overrides only the node profile for the
explicit board profiles; the rehearsal Pi address remains in this one file and must be checked
after DHCP changes.

The build profiles and reserved assignments are:

| PlatformIO environment | Emitted node ID | Board assignment |
|---|---|---|
| `physical-node` | `physical-001` | Backward-compatible alias for ESP32-B |
| `physical-node-001` | `physical-001` | ESP32-B, station MAC ending `52:98` |
| `physical-node-002` | `physical-002` | ESP32-C, station MAC ending `2a:54` |
| `physical-node-003` | `physical-003` | ESP32-D, station MAC ending `3d:a8` |

Each profile uses the same ignored `secrets.hpp` and gateway configuration. Confirm the attached
board's station MAC before every upload. Never flash ESP32-C or ESP32-D with the default
`physical-node` target now that unique profiles exist.

Sensor-only firmware can build without a secrets file; networking stays disabled. Before bring-up,
create the ignored credentials file:

```bash
cp firmware/include/secrets.example.hpp firmware/include/secrets.hpp
```

Replace both placeholders locally. Never commit `secrets.hpp`; firmware diagnostics never print its
contents. The Pi's current DHCP address, `192.168.1.187`, is stored only as `GATEWAY_HOST` in
`include/config.hpp`; recheck and update that one value whenever the Pi's lease changes.

## Host-testable logic

The portable tests can be compiled directly with the system C++ compiler:

```bash
firmware/scripts/run_host_tests.sh
```

The configured PlatformIO native environment runs the same custom tests:

```bash
pio test -d firmware -e native
```

The native environment is test-only. `pio run -d firmware -e native` is not a supported validation
command and is expected to lack an application entry point.

These tests validate encoding, newline framing, schema ranges, configuration, boot sequence, write
success semantics, and capped backoff. They do not claim to test Wi-Fi, I2C, or hardware.

## Planned I2C wiring for the six-pin breakout

Do not use the loose, unsoldered HW-611 header. After the replacement is available and with both
boards powered off, wire the pre-soldered BME280 as follows:

| ESP32 DevKit V1 | Six-pin BME280 | Purpose |
|---|---|---|
| `3V3` | `VCC` | 3.3 V sensor and interface power |
| `GND` | `GND` | common ground |
| `GPIO22` | `SCL` | I2C clock |
| `GPIO21` | `SDA` | I2C data |
| `3V3` | `CSB` | hold chip select at interface supply for I2C mode |
| `GND` | `SDO` | select I2C address `0x76` |

Bosch's
[BME280 datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf)
requires CSB to be connected directly to VDDIO in I2C mode. SDO is the address bit: ground selects
`0x76`, while VDDIO selects `0x77`. The table deliberately selects `0x76`; the firmware probes both
addresses, so SDO may instead be tied to 3V3 for `0x77`. Do not leave either strap ambiguous. Use
only 3.3 V with this unverified generic breakout. The BME280 bus also needs pull-ups to 3.3 V (4.7
kOhm is a normal Bosch value); inspect the replacement module for onboard pull-ups before adding
external ones.

## ESP32-only bring-up (no BME280)

From the repository root, activate the existing development environment and confirm the tool:

```bash
source .venv/bin/activate
pio --version
```

Before and after connecting a known data-capable USB-C cable, compare:

```bash
pio device list
system_profiler SPUSBDataType
ls /dev/cu.*
```

The CP2102 should appear in the USB report and as a new serial device, commonly containing
`usbserial` or `SLAB_USBtoUART`; use the port actually reported by `pio device list`. If a serial
device appears, do not install another driver. If the CP2102 appears in the USB tree but no serial
device is created, then check the current macOS compatibility notes and install the current Silicon
Labs CP210x VCP driver. First rule out a charge-only cable and USB adapter problem.

Build, upload, and monitor ESP32-B with the detected port substituted literally for `<PORT>`:

```bash
pio run -d firmware -e physical-node-001
pio run -d firmware -e physical-node-001 -t upload --upload-port <PORT>
pio device monitor -d firmware --port <PORT> --baud 115200
```

For ESP32-C use `physical-node-002`; for ESP32-D use `physical-node-003`. Build all three without
flashing by running:

```bash
pio run -d firmware -e physical-node-001
pio run -d firmware -e physical-node-002
pio run -d firmware -e physical-node-003
```

Do not include angle brackets in the real command. PlatformIO uses `esptool` by default for this
board. The CP2102 normally controls automatic reset; only if upload repeatedly waits for download
mode should the operator hold BOOT, start upload, release BOOT when connection begins, and press EN
afterward if needed.

The one-shot Wi-Fi scan and disconnect-reason callback remain available behind
`WIFI_BRINGUP_SCAN_ON_BOOT` and `WIFI_BRINGUP_REASON_DIAGNOSTIC` in `platformio.ini`. Both are
disabled by default after successful network bring-up. Temporarily set the relevant value to `1`,
rebuild, and flash only when diagnosing Wi-Fi; the scan prints visible SSIDs, so do not retain its
output as experiment evidence.

With no BME280, expected output includes the following. Wi-Fi/TCP lines appear only when the ignored
`secrets.hpp` has real credentials; retries may interleave.

```text
Hybrid Edge WSN physical-node firmware starting
Node ID: physical-001
Gateway target: 192.168.1.187:8662
timestamp_ms uses monotonic milliseconds since this ESP32 boot
Initializing I2C: SDA=GPIO21 SCL=GPIO22
I2C initialized
Trying BME280 at I2C address 0x76
Trying BME280 at I2C address 0x77
BME280 not detected at 0x76 or 0x77; will retry safely
Sensor unavailable: networking may continue; no readings will be emitted
Connecting to configured Wi-Fi network
Wi-Fi connected; ESP32 IP: <DHCP-assigned-address>
Connecting to gateway 192.168.1.187:8662
Gateway TCP connected
```

The address probes repeat every five seconds. There must be no `Sample`, `TX`, or JSON reading while
the sensor is absent. A TCP connection alone does not register `physical-001` in the gateway because
the node sends no fabricated application record. The gateway may close this silent socket after its
approximately 30-second idle timeout; a subsequent firmware TCP reconnect is expected and is not a
crash or reboot.

## Pre-sensor TCP rehearsal on the Pi

This is an engineering rehearsal, not a scientific experiment. Confirm that `wsn-edge` still owns
`192.168.1.187`, then run the following from the repository root on the Pi. Every invocation creates
a fresh temporary evidence directory, and each writer refuses to overwrite an existing file.

```bash
run_dir="$(mktemp -d /tmp/hybrid-edge-wsn-presensor.XXXXXX)"
echo "temporary evidence: $run_dir"
.venv/bin/python -m gateway.server \
  --host 0.0.0.0 \
  --port 8662 \
  --upstream-mode disabled \
  --raw-output "$run_dir/raw.ndjson" \
  --gateway-events-output "$run_dir/gateway-events.ndjson" \
  --health-events-output "$run_dir/health-events.ndjson" \
  --summary-output "$run_dir/summary.json" \
  --log-level INFO
```

With ESP32-C connected and no sensor attached, reset the ESP32 only after the gateway reports
`gateway listening on 0.0.0.0:8662`. Expected Serial evidence is:

```text
Wi-Fi connected; ESP32 IP: <DHCP-assigned-address>
Connecting to gateway 192.168.1.187:8662
Gateway TCP connected
Trying BME280 at I2C address 0x76
Trying BME280 at I2C address 0x77
BME280 not detected at 0x76 or 0x77; will retry safely
```

The BME280 probes repeat. There must be no `Sample`, `TX sequence=`, `Send success`, or NDJSON
reading. At INFO level, a silent TCP accept is intentionally not logged as a sensor observation.
After stopping the gateway with Ctrl+C, `gateway-events.ndjson` must contain at least one
`connection_accepted` event and may also contain idle/close events. `raw.ndjson` and
`health-events.ndjson` must both be empty. The summary must show at least one accepted connection,
`active_connections: 0`, `valid_messages: 0`, `sensor_bytes: 0`, an empty `registry`, and zero
storage records. In particular, `physical-001` must not appear in the registry because the gateway
creates node state only after parsing a valid application message.

To validate gateway loss and recovery, stop the first gateway with Ctrl+C while Serial remains open.
Expect `Gateway TCP disconnected`, failed connection attempts, and retry delays that grow from 1
second toward the 30-second cap. Restart the gateway by rerunning the complete command above so it
gets a new `run_dir`. Do not reuse the previous output paths. Without resetting the ESP32, wait up
to the current backoff interval and require another `Gateway TCP connected`. Stop the second gateway
and verify its fresh raw file and registry are also empty. This proves transport recovery only; it
does not prove sensing, delivery of a `ReadingMessage`, or scientific performance.

## BME280 bring-up

Sections A through D passed on ESP32-B on 2026-08-16. All bring-up checks are engineering
validation rather than a scientific experiment.

### A. Wire while powered off

Disconnect ESP32 USB power, then connect the pre-soldered sensor exactly as shown in the wiring table.
Recheck 3V3/GND orientation before applying power.

### B. Detect and validate the sensor

Power the ESP32 and monitor at 115200 baud. For the documented SDO-to-GND wiring, expect output like:

```text
Initializing I2C: SDA=GPIO21 SCL=GPIO22
I2C initialized
Trying BME280 at I2C address 0x76
BME280 detected at 0x76
Sample temperature=<finite> C humidity=<finite> % pressure=<finite> hPa
```

Confirm temperature is plausible for the room, humidity is between 0 and 100%, and local pressure is
plausible (typically near 1000 hPa, with altitude and weather effects). Stop if values are NaN,
outside firmware limits, or clearly implausible. Do not reinterpret a BMP280 lacking humidity as a
working BME280.

Observed on ESP32-B: detection at `0x76`, approximately 24.7–25.1 °C, 48.5–50.5% relative humidity,
and 1011.2–1011.3 hPa across repeated samples, with no invalid-reading or reinitialization message.

### C. Send the physical node to the Pi

Confirm `wsn-edge` still owns `192.168.1.187`, start its gateway listening on port 8662, then reboot
the ESP32. Serial should show Wi-Fi, its assigned IP, the configured gateway target, and
`Gateway TCP connected`. On the Pi verify accepted records have `node_id=physical-001`,
`node_kind=physical`, and sequences `0`, `1`, `2`, ... with no schema rejection.

This passed with upstream disabled: the Pi accepted 463 valid physical messages and persisted all
463 RAW records in a fresh temporary directory. The final summary reported zero invalid messages,
duplicates, out-of-order messages, and estimated missing messages. It correctly recognized one
deliberate ESP32 sequence reset, and the final sequence was `429`.

A persistent-client shutdown defect was exposed during this bring-up: the gateway awaited final
server closure before closing the active ESP32 stream. The corrected order is covered by a host
regression test. A follow-up Pi rehearsal sent SIGTERM while ESP32-B remained connected; the gateway
exited and wrote its summary without resetting the ESP32, preserving all 16 accepted records with
zero invalid, duplicate, out-of-order, or estimated-missing messages.

### D. Validate end to end in RAW mode

Run the Mac collector in the project's existing RAW workflow and verify the chain BME280 -> ESP32 ->
Wi-Fi -> Pi gateway -> Mac collector. Check both Pi persistence and collector record counts. This is a
bring-up validation, not a wireless-performance or latency claim.

This passed with 812 physical readings: the Pi persisted and forwarded all 812 records, and the Mac
collector received all 812. The Pi and collector summaries reported matching 361,242 upstream
application bytes, with zero invalid, overlong, or truncated messages and no upstream send failure,
abandonment, or queue drop. The preserved temporary evidence paths are recorded only as engineering
artifacts and are not scientific results.

### E. Validate failure and recovery

After several in-order readings, disconnect ESP32 power. Verify the Pi transitions the registered node
from ONLINE to SUSPECT to OFFLINE. Restore power and verify the reboot begins at sequence 0, the
gateway classifies it as a restart/reset, `physical-001` returns ONLINE, and subsequent readings are
IN_ORDER.

## Hardware risks still requiring observation

- Clone boards marked DEVKITV1 can differ in flash population and automatic-reset circuitry despite
  sharing the common form factor; the first upload is the definitive check.
- A charge-only USB-C cable or missing/incompatible CP210x VCP driver can hide the serial port.
- The generic BME280 breakout passed local detection, sampling, and bounded end-to-end RAW delivery
  at 3.3 V, but its long-duration stability remains unverified.
- The Pi address is DHCP-assigned and can change; verify it before each bring-up.
