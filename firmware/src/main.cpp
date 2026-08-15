#include <Adafruit_BME280.h>
#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_timer.h>

#include <cinttypes>
#include <cstring>
#include <initializer_list>

#include "config.hpp"
#include "node_protocol.hpp"

#if __has_include("secrets.hpp")
#include "secrets.hpp"
#else
namespace secrets {
inline constexpr char WIFI_SSID[] = "";
inline constexpr char WIFI_PASSWORD[] = "";
}  // namespace secrets
#endif

namespace {

Adafruit_BME280 bme280;
WiFiClient gateway_client;
hybrid_wsn::SequenceCounter sequence_counter;

bool sensor_ready = false;
bool configuration_ready = false;
bool network_ready = false;
bool wifi_attempt_active = false;
bool wifi_was_connected = false;
bool tcp_was_connected = false;
uint8_t sensor_address = 0;
uint64_t wifi_attempt_started_ms = 0;
uint64_t next_wifi_attempt_ms = 0;
uint64_t next_tcp_attempt_ms = 0;
uint64_t next_sensor_attempt_ms = 0;
uint64_t next_sample_ms = 0;
uint32_t wifi_backoff_ms = 0;
uint32_t tcp_backoff_ms = 0;

uint64_t uptime_ms() {
    return static_cast<uint64_t>(esp_timer_get_time()) / 1000ULL;
}

hybrid_wsn::RuntimeConfig runtime_config() {
    return {
        node_config::NODE_ID,
        node_config::GATEWAY_HOST,
        node_config::GATEWAY_PORT,
        node_config::SAMPLING_INTERVAL_MS,
        node_config::RECONNECT_INITIAL_MS,
        node_config::RECONNECT_MAX_MS,
    };
}

bool secrets_configured() {
    return std::strlen(secrets::WIFI_SSID) > 0 &&
           std::strcmp(secrets::WIFI_SSID, "REPLACE_WITH_WIFI_SSID") != 0 &&
           std::strlen(secrets::WIFI_PASSWORD) > 0 &&
           std::strcmp(secrets::WIFI_PASSWORD, "REPLACE_WITH_WIFI_PASSWORD") != 0;
}

bool initialize_sensor() {
    for (const uint8_t address : {static_cast<uint8_t>(0x76), static_cast<uint8_t>(0x77)}) {
        Serial.printf("Trying BME280 at I2C address 0x%02X\n", address);
        if (bme280.begin(address, &Wire)) {
            sensor_address = address;
            Serial.printf("BME280 detected at 0x%02X\n", sensor_address);
            return true;
        }
    }
    Serial.println("BME280 not detected at 0x76 or 0x77; will retry safely");
    return false;
}

void schedule_wifi_retry(uint64_t now_ms) {
    const uint32_t delay_ms = wifi_backoff_ms == 0
                                  ? node_config::RECONNECT_INITIAL_MS
                                  : wifi_backoff_ms;
    next_wifi_attempt_ms = now_ms + delay_ms;
    wifi_backoff_ms = hybrid_wsn::next_backoff_ms(
        delay_ms,
        node_config::RECONNECT_INITIAL_MS,
        node_config::RECONNECT_MAX_MS);
    Serial.printf("Wi-Fi retry in %u ms\n", delay_ms);
}

void schedule_tcp_retry(uint64_t now_ms) {
    const uint32_t delay_ms = tcp_backoff_ms == 0
                                  ? node_config::RECONNECT_INITIAL_MS
                                  : tcp_backoff_ms;
    next_tcp_attempt_ms = now_ms + delay_ms;
    tcp_backoff_ms = hybrid_wsn::next_backoff_ms(
        delay_ms,
        node_config::RECONNECT_INITIAL_MS,
        node_config::RECONNECT_MAX_MS);
    Serial.printf("Gateway TCP retry in %u ms\n", delay_ms);
}

void service_wifi(uint64_t now_ms) {
    if (WiFi.status() == WL_CONNECTED) {
        wifi_attempt_active = false;
        wifi_backoff_ms = 0;
        if (!wifi_was_connected) {
            wifi_was_connected = true;
            Serial.print("Wi-Fi connected; ESP32 IP: ");
            Serial.println(WiFi.localIP());
        }
        return;
    }

    if (wifi_was_connected) {
        wifi_was_connected = false;
        Serial.println("Wi-Fi disconnected");
        gateway_client.stop();
        tcp_was_connected = false;
        next_tcp_attempt_ms = 0;
    }

    if (wifi_attempt_active) {
        if (now_ms - wifi_attempt_started_ms < node_config::WIFI_CONNECT_TIMEOUT_MS) {
            return;
        }
        Serial.println("Wi-Fi connection attempt timed out");
        WiFi.disconnect();
        wifi_attempt_active = false;
        schedule_wifi_retry(now_ms);
        return;
    }

    if (now_ms < next_wifi_attempt_ms) {
        return;
    }
    Serial.println("Connecting to configured Wi-Fi network");
    WiFi.begin(secrets::WIFI_SSID, secrets::WIFI_PASSWORD);
    wifi_attempt_active = true;
    wifi_attempt_started_ms = now_ms;
}

void service_tcp(uint64_t now_ms) {
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }
    if (gateway_client.connected()) {
        if (!tcp_was_connected) {
            tcp_was_connected = true;
            tcp_backoff_ms = 0;
            next_sample_ms = now_ms;
            Serial.println("Gateway TCP connected");
        }
        return;
    }

    if (tcp_was_connected) {
        tcp_was_connected = false;
        Serial.println("Gateway TCP disconnected");
    }
    gateway_client.stop();
    if (now_ms < next_tcp_attempt_ms) {
        return;
    }

    Serial.printf(
        "Connecting to gateway %s:%u\n",
        node_config::GATEWAY_HOST,
        node_config::GATEWAY_PORT);
    gateway_client.setTimeout(node_config::TCP_CONNECT_TIMEOUT_MS);
    if (gateway_client.connect(
            node_config::GATEWAY_HOST,
            node_config::GATEWAY_PORT,
            node_config::TCP_CONNECT_TIMEOUT_MS)) {
        gateway_client.setNoDelay(true);
        tcp_was_connected = true;
        tcp_backoff_ms = 0;
        next_sample_ms = now_ms;
        Serial.println("Gateway TCP connected");
        return;
    }
    Serial.println("Gateway TCP connection failed");
    schedule_tcp_retry(now_ms);
}

void sample_and_maybe_send(uint64_t now_ms) {
    if (!sensor_ready || now_ms < next_sample_ms) {
        return;
    }
    next_sample_ms = now_ms + node_config::SAMPLING_INTERVAL_MS;

    const double temperature_c = bme280.readTemperature();
    const double humidity_pct = bme280.readHumidity();
    const double pressure_hpa = bme280.readPressure() / 100.0;
    const hybrid_wsn::ReadingValues values{
        temperature_c,
        humidity_pct,
        pressure_hpa,
    };
    if (!hybrid_wsn::valid_reading(values)) {
        Serial.println("BME280 returned an invalid reading; will reinitialize safely");
        sensor_ready = false;
        next_sensor_attempt_ms = now_ms + node_config::SENSOR_RETRY_MS;
        return;
    }

    const uint64_t sequence = sequence_counter.current();
    Serial.printf(
        "Sample temperature=%.3f C humidity=%.3f %% pressure=%.3f hPa\n",
        temperature_c,
        humidity_pct,
        pressure_hpa);
    if (!gateway_client.connected()) {
        Serial.printf(
            "Sample not sent: gateway disconnected; no queue; sequence remains %" PRIu64 "\n",
            sequence);
        return;
    }

    const std::string payload = hybrid_wsn::encode_reading_ndjson(
        node_config::NODE_ID,
        sequence,
        now_ms,
        values);
    if (payload.empty()) {
        Serial.println("Failed to encode a valid reading; nothing sent");
        return;
    }

    Serial.printf(
        "TX sequence=%" PRIu64 " temperature=%.3f C humidity=%.3f %% pressure=%.3f hPa\n",
        sequence,
        temperature_c,
        humidity_pct,
        pressure_hpa);
    const size_t written = gateway_client.write(
        reinterpret_cast<const uint8_t *>(payload.data()), payload.size());
    if (written == payload.size() && gateway_client.connected()) {
        sequence_counter.mark_write_succeeded();
        Serial.printf("Send success: %u NDJSON bytes\n", static_cast<unsigned>(written));
        return;
    }

    Serial.printf(
        "Send failure: wrote %u of %u bytes; sequence will be reused\n",
        static_cast<unsigned>(written),
        static_cast<unsigned>(payload.size()));
    gateway_client.stop();
    tcp_was_connected = false;
    schedule_tcp_retry(now_ms);
}

}  // namespace

void setup() {
    Serial.begin(node_config::SERIAL_BAUD);
    delay(250);
    Serial.println();
    Serial.println("Hybrid Edge WSN physical-node firmware starting");
    Serial.printf("Node ID: %s\n", node_config::NODE_ID);
    Serial.printf(
        "Gateway target: %s:%u\n",
        node_config::GATEWAY_HOST,
        node_config::GATEWAY_PORT);
    Serial.println("timestamp_ms uses monotonic milliseconds since this ESP32 boot");

    if (!hybrid_wsn::valid_runtime_config(runtime_config())) {
        Serial.println("FATAL: invalid non-secret node configuration");
        return;
    }
    configuration_ready = true;

    Wire.begin();
    sensor_ready = initialize_sensor();
    if (!sensor_ready) {
        next_sensor_attempt_ms = uptime_ms() + node_config::SENSOR_RETRY_MS;
    }
    if (secrets_configured()) {
        network_ready = true;
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(false);
    } else {
        Serial.println(
            "Wi-Fi disabled: copy secrets.example.hpp to secrets.hpp and set credentials");
        Serial.println("Sensor-only Serial bring-up remains active");
    }
}

void loop() {
    const uint64_t now_ms = uptime_ms();
    if (!configuration_ready) {
        delay(1000);
        return;
    }
    if (!sensor_ready) {
        if (now_ms >= next_sensor_attempt_ms) {
            sensor_ready = initialize_sensor();
            next_sensor_attempt_ms = now_ms + node_config::SENSOR_RETRY_MS;
        }
        delay(10);
        return;
    }

    if (network_ready) {
        service_wifi(now_ms);
        service_tcp(now_ms);
    }
    sample_and_maybe_send(uptime_ms());
    delay(10);
}
