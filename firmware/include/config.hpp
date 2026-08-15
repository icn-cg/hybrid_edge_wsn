#pragma once

#include <cstdint>

namespace node_config {

inline constexpr char NODE_ID[] = "physical-001";
inline constexpr char GATEWAY_HOST[] = "192.168.1.187";
inline constexpr uint16_t GATEWAY_PORT = 8662;
inline constexpr uint32_t SAMPLING_INTERVAL_MS = 1000;
inline constexpr uint32_t SERIAL_BAUD = 115200;
inline constexpr uint32_t SENSOR_RETRY_MS = 5000;
inline constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;
inline constexpr uint32_t TCP_CONNECT_TIMEOUT_MS = 3000;
inline constexpr uint32_t RECONNECT_INITIAL_MS = 1000;
inline constexpr uint32_t RECONNECT_MAX_MS = 30000;

}  // namespace node_config
