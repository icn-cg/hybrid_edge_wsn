#!/usr/bin/env bash
set -euo pipefail

firmware_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_binary="${TMPDIR:-/tmp}/hybrid_edge_wsn_firmware_host_tests"

"${CXX:-c++}" \
  -std=c++17 \
  -Wall \
  -Wextra \
  -Werror \
  -I"${firmware_dir}/include" \
  -I"${firmware_dir}/lib/NodeProtocol/src" \
  "${firmware_dir}/lib/NodeProtocol/src/node_protocol.cpp" \
  "${firmware_dir}/test/test_node_protocol/test_main.cpp" \
  -o "${test_binary}"

"${test_binary}" "$@"
