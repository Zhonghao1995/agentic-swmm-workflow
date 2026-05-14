#!/usr/bin/env bash
# tests/test_install_script_prereq_python.bash
#
# Verifies that scripts/install.sh aborts cleanly with a remediation hint
# when Python is older than 3.10, instead of crashing partway through.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

# shellcheck source=../scripts/_install_test_harness.bash
source "$REPO_ROOT/scripts/_install_test_harness.bash"

trap 'harness_teardown' EXIT

harness_setup
harness_set_python "3.9"

run_install --auto

# Exit cleanly (non-zero, but not a hard crash) and surface remediation.
assert_status 2
assert_log_contains "Python 3.10"
assert_log_contains "brew install python@3.12"
assert_log_contains "apt install python3.12"
# Must never reach the "creating virtualenv" step.
assert_log_not_contains "Creating virtualenv"

echo "OK test_install_script_prereq_python"
