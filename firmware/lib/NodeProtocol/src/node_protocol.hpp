#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace hybrid_wsn {

inline constexpr uint8_t PROTOCOL_VERSION = 1;
inline constexpr std::size_t MAX_NODE_ID_LENGTH = 64;
inline constexpr std::size_t MAX_READING_NDJSON_BYTES = 512;

struct ReadingValues {
    double temperature_c;
    double humidity_pct;
    double pressure_hpa;
};

struct RuntimeConfig {
    const char *node_id;
    const char *gateway_host;
    uint16_t gateway_port;
    uint32_t sampling_interval_ms;
    uint32_t reconnect_initial_ms;
    uint32_t reconnect_max_ms;
};

bool valid_node_id(const char *node_id);
bool valid_reading(const ReadingValues &values);
bool valid_runtime_config(const RuntimeConfig &config);

std::string encode_reading_ndjson(
    const char *node_id,
    uint64_t sequence,
    uint64_t timestamp_ms,
    const ReadingValues &values);

uint32_t next_backoff_ms(uint32_t current_ms, uint32_t initial_ms, uint32_t maximum_ms);

class SequenceCounter {
  public:
    uint64_t current() const;
    bool mark_write_succeeded();

  private:
    uint64_t value_ = 0;
};

}  // namespace hybrid_wsn
