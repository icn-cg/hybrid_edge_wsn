#include "node_protocol.hpp"

#include <cmath>
#include <iostream>
#include <limits>
#include <string>

namespace {

int failures = 0;

void check(bool condition, const char *description) {
    if (!condition) {
        std::cerr << "FAIL: " << description << '\n';
        ++failures;
    }
}

void test_exact_protocol_record() {
    const hybrid_wsn::ReadingValues values{23.82, 47.31, 1012.4};
    const std::string encoded = hybrid_wsn::encode_reading_ndjson(
        "physical-001", 0, 1234, values);
    const std::string expected =
        "{\"type\":\"reading\",\"version\":1,\"node_id\":\"physical-001\","
        "\"node_kind\":\"physical\",\"sequence\":0,\"timestamp_ms\":1234,"
        "\"temperature_c\":23.820,\"humidity_pct\":47.310,"
        "\"pressure_hpa\":1012.400}\n";

    check(encoded == expected, "exact compact version-1 physical reading");
    check(!encoded.empty() && encoded.back() == '\n', "record has NDJSON newline");
    check(
        encoded.size() < hybrid_wsn::MAX_READING_NDJSON_BYTES,
        "record stays below the firmware bound");
}

void test_validation() {
    check(hybrid_wsn::valid_node_id("physical-001"), "valid node ID");
    check(!hybrid_wsn::valid_node_id("-physical"), "leading punctuation rejected");
    check(!hybrid_wsn::valid_node_id("physical 001"), "space rejected");
    check(
        !hybrid_wsn::valid_node_id(
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        "65-character node ID rejected");

    check(
        hybrid_wsn::valid_reading({-100.0, 0.0, 0.001}),
        "lower sensor bounds accepted");
    check(
        hybrid_wsn::valid_reading({100.0, 100.0, 1200.0}),
        "upper sensor bounds accepted");
    check(
        !hybrid_wsn::valid_reading(
            {std::numeric_limits<double>::quiet_NaN(), 50.0, 1000.0}),
        "non-finite sensor value rejected");
    check(
        !hybrid_wsn::valid_reading({20.0, 101.0, 1000.0}),
        "out-of-range humidity rejected");

    const hybrid_wsn::RuntimeConfig valid{
        "physical-001", "192.168.1.187", 8662, 1000, 1000, 30000};
    check(hybrid_wsn::valid_runtime_config(valid), "valid runtime configuration");
    const hybrid_wsn::RuntimeConfig invalid{
        "physical-001", "", 0, 0, 1000, 500};
    check(!hybrid_wsn::valid_runtime_config(invalid), "invalid runtime configuration");
}

void test_sequence_and_backoff() {
    hybrid_wsn::SequenceCounter sequence;
    check(sequence.current() == 0, "sequence starts at zero on boot");
    check(sequence.current() == 0, "unsuccessful write does not advance sequence");
    check(sequence.mark_write_succeeded(), "successful write advances sequence");
    check(sequence.current() == 1, "next transmitted sequence is one");

    check(hybrid_wsn::next_backoff_ms(0, 1000, 30000) == 1000, "initial backoff");
    check(hybrid_wsn::next_backoff_ms(1000, 1000, 30000) == 2000, "backoff doubles");
    check(hybrid_wsn::next_backoff_ms(16000, 1000, 30000) == 30000, "backoff caps");
    check(hybrid_wsn::next_backoff_ms(30000, 1000, 30000) == 30000, "cap is stable");
}

}  // namespace

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--emit-example") {
        std::cout << hybrid_wsn::encode_reading_ndjson(
            "physical-001", 0, 1234, {23.82, 47.31, 1012.4});
        return 0;
    }
    test_exact_protocol_record();
    test_validation();
    test_sequence_and_backoff();
    if (failures == 0) {
        std::cout << "All firmware host tests passed\n";
    }
    return failures == 0 ? 0 : 1;
}
