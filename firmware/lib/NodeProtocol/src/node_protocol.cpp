#include "node_protocol.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>

namespace hybrid_wsn {

namespace {

bool ascii_alphanumeric(unsigned char character) {
    return (character >= 'A' && character <= 'Z') ||
           (character >= 'a' && character <= 'z') ||
           (character >= '0' && character <= '9');
}

}  // namespace

bool valid_node_id(const char *node_id) {
    if (node_id == nullptr) {
        return false;
    }
    const std::size_t length = std::strlen(node_id);
    if (length == 0 || length > MAX_NODE_ID_LENGTH ||
        !ascii_alphanumeric(static_cast<unsigned char>(node_id[0]))) {
        return false;
    }
    for (std::size_t index = 1; index < length; ++index) {
        const unsigned char character = static_cast<unsigned char>(node_id[index]);
        if (!ascii_alphanumeric(character) && character != '_' && character != '.' &&
            character != '-') {
            return false;
        }
    }
    return true;
}

bool valid_reading(const ReadingValues &values) {
    return std::isfinite(values.temperature_c) && values.temperature_c >= -100.0 &&
           values.temperature_c <= 100.0 && std::isfinite(values.humidity_pct) &&
           values.humidity_pct >= 0.0 && values.humidity_pct <= 100.0 &&
           std::isfinite(values.pressure_hpa) && values.pressure_hpa > 0.0 &&
           values.pressure_hpa <= 1200.0;
}

bool valid_runtime_config(const RuntimeConfig &config) {
    return valid_node_id(config.node_id) && config.gateway_host != nullptr &&
           config.gateway_host[0] != '\0' && config.gateway_port > 0 &&
           config.sampling_interval_ms > 0 && config.reconnect_initial_ms > 0 &&
           config.reconnect_max_ms >= config.reconnect_initial_ms;
}

std::string encode_reading_ndjson(
    const char *node_id,
    uint64_t sequence,
    uint64_t timestamp_ms,
    const ReadingValues &values) {
    if (!valid_node_id(node_id) || !valid_reading(values)) {
        return {};
    }

    char output[MAX_READING_NDJSON_BYTES];
    const int written = std::snprintf(
        output,
        sizeof(output),
        "{\"type\":\"reading\",\"version\":%u,\"node_id\":\"%s\","
        "\"node_kind\":\"physical\",\"sequence\":%llu,\"timestamp_ms\":%llu,"
        "\"temperature_c\":%.3f,\"humidity_pct\":%.3f,\"pressure_hpa\":%.3f}\n",
        static_cast<unsigned>(PROTOCOL_VERSION),
        node_id,
        static_cast<unsigned long long>(sequence),
        static_cast<unsigned long long>(timestamp_ms),
        values.temperature_c,
        values.humidity_pct,
        values.pressure_hpa);
    if (written <= 0 || static_cast<std::size_t>(written) >= sizeof(output)) {
        return {};
    }
    return std::string(output, static_cast<std::size_t>(written));
}

uint32_t next_backoff_ms(uint32_t current_ms, uint32_t initial_ms, uint32_t maximum_ms) {
    if (initial_ms == 0 || maximum_ms < initial_ms) {
        return 0;
    }
    if (current_ms < initial_ms) {
        return initial_ms;
    }
    if (current_ms >= maximum_ms || current_ms > maximum_ms / 2) {
        return maximum_ms;
    }
    return std::min(current_ms * 2, maximum_ms);
}

uint64_t SequenceCounter::current() const {
    return value_;
}

bool SequenceCounter::mark_write_succeeded() {
    if (value_ == std::numeric_limits<uint64_t>::max()) {
        return false;
    }
    ++value_;
    return true;
}

}  // namespace hybrid_wsn
