#!/usr/bin/env bash
# tests/test_install_script_prompts.bash
#
# Exercises the stepped install flow's interactive surface:
#   - --auto runs to completion with no prompts
#   - Y at the risk warning + Y at every step completes
#   - N at the risk warning aborts with exit 0
#   - N at the MCP step aborts mid-flow with exit 0
#   - mocked `npm install` failure surfaces remediation + non-zero exit
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

# shellcheck source=../scripts/_install_test_harness.bash
source "$REPO_ROOT/scripts/_install_test_harness.bash"

cleanup() { harness_teardown; }
trap cleanup EXIT

# --- 1. --auto: no prompts, all 5 steps run -------------------------------
harness_setup
run_install --auto
assert_status 0
assert_log_contains "Step 1/5"
assert_log_contains "Step 2/5"
assert_log_contains "Step 3/5"
assert_log_contains "Step 4/5"
assert_log_contains "Step 5/5"
assert_log_contains "Install complete"
# In --auto mode the prompt prefix must never appear.
assert_log_not_contains "Continue with installation?"
harness_teardown

# --- 2. Y at risk warning + Y at every step --------------------------------
harness_setup
HARNESS_STDIN=$'y\ny\ny\ny\ny\ny\n' run_install
assert_status 0
assert_log_contains "Continue with installation?"
assert_log_contains "Step 1/5"
assert_log_contains "Step 5/5"
assert_log_contains "Install complete"
harness_teardown

# --- 3. N at the risk warning ---------------------------------------------
harness_setup
HARNESS_STDIN=$'n\n' run_install
assert_status 0
assert_log_contains "Continue with installation?"
assert_log_contains "Installation aborted"
assert_log_not_contains "Step 1/5"
harness_teardown

# --- 4. N at the MCP step --------------------------------------------------
harness_setup
# Y to continue, Y to venv, Y to deps, N to MCP step.
HARNESS_STDIN=$'y\ny\ny\nn\n' run_install
assert_status 0
assert_log_contains "Step 3/5"
assert_log_contains "Installation aborted"
# Steps 4 and 5 must not have run.
assert_log_not_contains "Step 4/5"
harness_teardown

# --- 5. npm install failure ------------------------------------------------
harness_setup
harness_set_npm_fails 1
run_install --auto
# Non-zero exit, clear remediation hint, raw "mock npm failure" is allowed
# to appear but the user-facing message must include guidance.
if [[ "$INSTALL_STATUS" == "0" ]]; then
  echo "FAIL: expected non-zero exit on npm failure, got 0" >&2
  exit 1
fi
assert_log_contains "MCP server install failed"
assert_log_contains "npm"
harness_teardown

echo "OK test_install_script_prompts"
