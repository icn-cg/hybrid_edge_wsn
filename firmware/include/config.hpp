#pragma once

#include <cstdint>

#ifndef HYBRID_WSN_NODE_PROFILE
#define HYBRID_WSN_NODE_PROFILE 1
#endif

namespace node_config {

#if HYBRID_WSN_NODE_PROFILE == 1
inline constexpr char NODE_ID[] = "physical-001";
#elif HYBRID_WSN_NODE_PROFILE == 2
inline constexpr char NODE_ID[] = "physical-002";
#elif HYBRID_WSN_NODE_PROFILE == 3
inline constexpr char NODE_ID[] = "physical-003";
#else
#error "HYBRID_WSN_NODE_PROFILE must be 1, 2, or 3"
#endif
inline constexpr char GATEWAY_HOST[] = "192.168.1.187";
inline constexpr uint16_t GATEWAY_PORT = 8662;
inline constexpr uint32_t SAMPLING_INTERVAL_MS = 1000;
inline constexpr uint32_t SERIAL_BAUD = 115200;
inline constexpr int I2C_SDA_PIN = 21;
inline constexpr int I2C_SCL_PIN = 22;
inline constexpr uint32_t SENSOR_RETRY_MS = 5000;
inline constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;
inline constexpr uint32_t TCP_CONNECT_TIMEOUT_MS = 3000;
inline constexpr uint32_t RECONNECT_INITIAL_MS = 1000;
inline constexpr uint32_t RECONNECT_MAX_MS = 30000;

}  // namespace node_config
