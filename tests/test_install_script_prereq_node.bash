#!/usr/bin/env bash
# tests/test_install_script_prereq_node.bash
#
# Verifies that scripts/install.sh aborts cleanly with a remediation hint
# when Node is older than 18.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

# shellcheck source=../scripts/_install_test_harness.bash
source "$REPO_ROOT/scripts/_install_test_harness.bash"

trap 'harness_teardown' EXIT

harness_setup
harness_set_node "16"

run_install --auto

assert_status 2
assert_log_contains "Node 18"
assert_log_contains "brew install node"
assert_log_contains "apt install nodejs"
assert_log_not_contains "Creating virtualenv"

echo "OK test_install_script_prereq_node"
