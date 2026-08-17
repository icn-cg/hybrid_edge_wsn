# Project device registry

This file identifies the physical compute, network, node, sensor, and support devices used or
evaluated for the Hybrid Edge WSN project. Wi-Fi credentials are never recorded here. Values such
as DHCP addresses and serial-device paths are observations, not permanent identities.

## Registry summary

| Device ID | Device | Project role | Status |
|---|---|---|---|
| `MAC-DEV` | Apple silicon Mac | Development host, ESP32 flashing/monitoring, upstream collector | Active |
| `PI-EDGE` | Raspberry Pi 4, approximately 4 GB RAM | `wsn-edge` gateway host | Active; software path validated |
| `ESP32-A` | Generic ESP32 DevKit V1 | Initial physical-node candidate | Quarantined; Wi-Fi hardware failure |
| `ESP32-B` | Generic ESP32 DevKit V1 | Active `physical-001` node | Wi-Fi bring-up passed |
| `ESP32-C` | Generic ESP32 DevKit V1 | Reserved `physical-002` node | Wi-Fi passed; unique profile built, not yet flashed |
| `ESP32-D` | Generic ESP32 DevKit V1 | Reserved `physical-003` node | Wi-Fi passed; unique profile built, not yet flashed |
| `BME-HW611` | Generic HW-611 BME280 breakout | Initial physical sensor candidate | Quarantined; unsoldered header |
| `BME-6PIN` | Pre-soldered six-pin BME280 breakout | Sensor on ESP32-B / `physical-001` | Local and end-to-end RAW bring-up passed |
| `AP-PRIMARY` | Spectrum-managed access point, exact model unknown | Primary 2.4 GHz experiment network | Active; ESP32-B association passed |
| `AP-ALT` | Alternate Spectrum/Orbi access point, exact model unknown | Secondary Wi-Fi diagnostic network | Diagnostic use only |
| `AP-HOTSPOT` | iPhone Personal Hotspot, exact phone model unknown | Independent 2.4 GHz WPA2 diagnostic network | Diagnostic use only |
| `USB-CABLE-1` | Anker-branded USB data/power cable, exact model unknown | ESP32 power, flash, and Serial | Upload validated with all four ESP32 boards |

Software-only virtual nodes are not physical devices and are intentionally excluded from this
registry.

## Compute hosts

### MAC-DEV — development and collector host

The active development host is an Apple silicon (`arm64`) Mac running macOS 26.3.1 and Python
3.13.13 in the validated project environment. It builds and tests the Python services and firmware,
flashes and monitors ESP32 boards over USB, runs virtual nodes, and hosts the upstream collector in
the real multi-machine rehearsal. Its hardware model, hardware serial number, and stable network
identity have not been recorded.

### PI-EDGE — Raspberry Pi gateway

`wsn-edge` is a Raspberry Pi 4 with approximately 4 GB RAM, `aarch64` Debian 13 / Raspberry Pi OS,
and Python 3.13.5. It hosts the edge gateway. DHCP address `192.168.1.187` was observed during Phase
4 and is the current firmware target, but the lease must be rechecked before each physical run.

The Pi passed dependency installation, Ruff, and 99 of 100 tests before the remaining failure was
identified as a stale test monotonic timestamp; the corrected cross-platform suite later passed 103
tests on the Mac, and the current suite passes 104 after the persistent-client shutdown regression
was added. The real Mac-to-Pi-to-Mac RAW and AGGREGATED software rehearsal also passed. Physical
BME280 readings have been persisted on the Pi and forwarded end to end to the Mac collector.

## ESP32 boards

| Board | ESP32 station MAC | Assignment | Status |
|---|---|---|---|
| ESP32-A | `8c:94:df:45:c4:b4` | Quarantined; do not use for Wi-Fi experiments | Wi-Fi authentication hardware failure |
| ESP32-B | `8c:94:df:46:52:98` | Active `physical-001` node | Wi-Fi bring-up passed |
| ESP32-C | `8c:94:df:4d:2a:54` | Reserved `physical-002` node | Wi-Fi passed; profile built, not yet flashed |
| ESP32-D | `58:2a:bd:74:3d:a8` | Reserved `physical-003` node | Wi-Fi passed; profile built, not yet flashed |

All four boards enumerated through a CP2102 USB-to-UART bridge as VID:PID `10c4:ea60` and
`/dev/cu.usbserial-0001`. All reported an ESP32-D0WD-V3 revision 3.1, a 40 MHz crystal, and 4 MB
flash. Those shared USB and chip attributes are not unique board identifiers.

## ESP32-A — quarantined

On 2026-08-15 and 2026-08-16, this board could scan 2.4 GHz networks and boot, flash, initialize
I2C, and run the sensor-absent retry path. It repeatedly failed station authentication with
disconnect reason `2` (`AUTH_EXPIRE`) against three independently tested access points, including a
WPA2 phone hotspot at strong RSSI.

The same failure occurred with a minimal sketch containing only `WiFi.begin()`. A full 4 MB chip
erase followed by a clean minimal-sketch flash regenerated NVS/RF calibration state but did not
change the result. Comparison with ESP32-B using the same cable, port, credentials, framework, and
project firmware isolated the fault to ESP32-A's Wi-Fi hardware or RF path. Keep the board only for
non-Wi-Fi diagnostics unless independently repaired and requalified.

After ESP32-B and ESP32-C both passed with the same setup, ESP32-A was identified again by its MAC
and retested on 2026-08-16 while removed from the breadboard. It saw the configured target at
`-57 dBm` on channel 1 but again produced repeated `AUTH_EXPIRE` events and timed out. This rules
out an ongoing breadboard-contact or pin-placement condition; prior electrical damage cannot be
confirmed or excluded.

## ESP32-B — active

On 2026-08-16, this replacement board was flashed with the project `physical-node` firmware. It
found the configured 2.4 GHz WPA2 network at `-53 dBm` on channel 1, completed Wi-Fi association,
and obtained DHCP address `192.168.1.55` during the test. The DHCP address is observational and must
not be treated as a fixed configuration value.

The board then attempted the configured gateway at `192.168.1.187:8662`. TCP retries were expected
because no gateway process was listening on port 8662 during this comparison. BME280 validation was
also not part of the board comparison because the sensor was absent.

On 2026-08-16, the replacement BME280 was subsequently wired to ESP32-B at 3.3 V. The clean default
firmware detected it at I2C address `0x76` and produced stable finite local samples. No gateway was
available, so the firmware correctly left the samples unsent and kept sequence at `0`.

The Pi gateway was then started on `192.168.1.187:8662` with upstream disabled. ESP32-B connected
and reported successful NDJSON writes. The final summary contained 463 valid messages and 463
persisted RAW records, with zero invalid, duplicate, out-of-order, or estimated-missing messages.
One deliberate ESP32 reset was correctly classified as a sequence reset; the final sequence was
`429`. A later end-to-end RAW rehearsal persisted and forwarded 812 of 812 physical records from
the Pi and collected all 812 on the Mac, with matching 361,242 upstream application bytes and no
invalid, truncated, or overlong messages. These are engineering validations, not scientific
performance results.

## ESP32-C — validated spare

On 2026-08-16, the third board was flashed with the same project `physical-node` firmware. Its
station MAC is `8c:94:df:4d:2a:54`. It found the configured target at `-63 dBm` on channel 1,
completed Wi-Fi association, and obtained DHCP address `192.168.1.191`. The address is only the
observed lease from this test.

The driver reported one transient `AUTH_FAIL` during its internal first-connection retry, then
connected successfully. Gateway TCP attempts failed only because nothing was listening at
`192.168.1.187:8662`. The sensor was absent, so BME280 validation remains pending.

ESP32-C currently contains the older firmware configured as `physical-001`. Its reserved assignment
is now `physical-002`, provided by PlatformIO environment `physical-node-002`, but that image has not
yet been flashed. Do not power it on concurrently with ESP32-B on the experiment network until the
unique profile has been uploaded and Serial confirms `Node ID: physical-002`; duplicate physical
node IDs would make gateway records scientifically ambiguous.

Confirm the attached station MAC ends in `2a:54` before uploading the ESP32-C profile.

## ESP32-D — validated spare

On 2026-08-16, this newly acquired board was identified as an ESP32-D0WD-V3 revision 3.1 with a
40 MHz crystal and station MAC `58:2a:bd:74:3d:a8`. The existing `esp32doit-devkit-v1` target flashed
successfully and the uploaded image passed hash verification. The firmware then booted, initialized
I2C, handled the absent BME280 safely, associated with the configured Wi-Fi network, and obtained
DHCP address `192.168.1.56`. The address is only the observed lease from this test.

The board attempted the configured gateway at `192.168.1.187:8662` and followed the bounded TCP
retry schedule when the remote endpoint reset the connection. No BME280 was connected, so it
emitted no sensor reading and does not constitute sensor validation. ESP32-D currently contains the
older firmware configured as `physical-001`. Its reserved assignment is `physical-003`, provided by
PlatformIO environment `physical-node-003`, but that image has not yet been flashed. Do not operate
it alongside ESP32-B until Serial confirms `Node ID: physical-003`; confirm the station MAC ends in
`3d:a8` before uploading.

## Sensor hardware

### BME-HW611 — quarantined

The initial generic HW-611 BME280 breakout has a loose, unsoldered header. It was explicitly rejected
for physical validation because intermittent header contact would make I2C evidence unreliable. Do
not use it for experiment data unless the header is properly soldered and the board is independently
requalified.

### BME-6PIN — local bring-up validated

The replacement received on 2026-08-16 is a generic pre-soldered six-pin breakout labeled for
`VCC`, `GND`, `SCL`, `SDA`, `CSB`, and `SDO`. A visual inspection confirms the expected labels and
installed header. It is wired to ESP32-B using 3.3 V, GPIO22 for SCL, GPIO21 for SDA, CSB tied to
3.3 V, and SDO tied to ground for address `0x76`.

The Adafruit BME280 driver detected the device at `0x76`. Repeated local samples were stable and
finite: approximately 24.7–25.1 °C, 48.5–50.5% relative humidity, and 1011.2–1011.3 hPa. No invalid
reading or sensor reinitialization occurred during the bounded captures. A later engineering check
persisted all 463 accepted physical RAW readings on the Pi gateway with upstream disabled. A later
bounded rehearsal delivered 812 of 812 records through the Pi to the Mac collector. Scientific
performance remains unvalidated.

## Network devices used for diagnosis

`AP-PRIMARY` is the network on which ESP32-B completed WPA2 association and DHCP. During that test,
the configured target was observed at `-53 dBm` on channel 1. Credentials and SSIDs belong only in
the ignored local `secrets.hpp` and are never copied into this registry.

`AP-ALT` was used to determine whether ESP32-A's repeated `AUTH_EXPIRE` result was specific to the
primary access point. The target was visible at `-74 dBm` on channel 9, but ESP32-A still failed.

`AP-HOTSPOT` provided an independent non-router test. With compatibility mode enabled, ESP32-A saw
the WPA2 hotspot at `-37 dBm` on channel 6 but again returned `AUTH_EXPIRE`. The hotspot test also
confirmed that SSIDs containing typographic punctuation must match byte-for-byte; no network name or
password is retained here.

The exact access-point and iPhone models, firmware versions, administrative settings, and stable
hardware identifiers are unknown because administrative access was unavailable during bring-up.

## USB and identification notes

All four ESP32 boards enumerated through an onboard CP2102 USB-to-UART bridge as VID:PID `10c4:ea60`,
USB serial string `0001`, and `/dev/cu.usbserial-0001`. These values repeated across boards and are
therefore unsuitable as inventory keys. The Anker-branded USB cable successfully powered, flashed,
and monitored all four boards; no separate cable serial number is available.

Use the ESP32 station MAC address to distinguish the ESP32 boards. For other device classes,
prefer a stable hostname or explicitly assigned inventory label. Do not put account credentials,
Wi-Fi credentials, device unlock codes, or private hardware serial numbers in this repository.

## Updating this registry

For each newly introduced physical device, record its type, project role, stable non-secret
identifier when available, date tested, and specific bring-up stages passed. Reassigning
`physical-001`, replacing the gateway, or changing the collector host requires updating this
registry and verifying the corresponding non-secret configuration separately.
