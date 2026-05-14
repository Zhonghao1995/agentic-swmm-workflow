#!/usr/bin/env bash
# scripts/_install_test_harness.bash
#
# Test harness for the stepped install flow. Builds an isolated sandbox
# containing mock python / pip / node / npm / git / cmake / brew / find
# binaries so install.sh can be exercised end-to-end without touching the
# real toolchain.
#
# Source this file from a test script, set the desired mock_* variables,
# then call `run_install <args...>`. Stdout/stderr go through the install
# script verbatim; the exit code is exposed in $INSTALL_STATUS.

# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------
#
#   source "scripts/_install_test_harness.bash"
#   harness_setup            # creates $SANDBOX with mock $PATH
#   harness_set_python 3.12  # default; pass "3.9" to simulate too-old python
#   harness_set_node 20      # default; pass "16" to simulate too-old node
#   harness_set_npm_fails 0  # default; "1" makes mock npm install fail
#   run_install --auto       # run install.sh, captures status into $INSTALL_STATUS
#   harness_teardown         # rm -rf the sandbox
#
# All log/spinner output from the install is preserved in $INSTALL_LOG so
# assertions can grep for specific phrases.

HARNESS_DIR="${BASH_SOURCE%/*}"
HARNESS_DIR="$(cd "$HARNESS_DIR" && pwd)"
REPO_ROOT="$(cd "$HARNESS_DIR/.." && pwd)"

SANDBOX=""
INSTALL_STATUS=0
INSTALL_LOG=""

# Defaults that individual tests can override.
MOCK_PYTHON_VERSION="3.12"
MOCK_NODE_VERSION="20"
MOCK_NPM_FAILS=0
MOCK_PIP_FAILS=0

harness_setup() {
  SANDBOX="$(mktemp -d -t aiswmm-install-test.XXXXXX)"
  mkdir -p "$SANDBOX/bin"
  mkdir -p "$SANDBOX/home"
  mkdir -p "$SANDBOX/cache"
  # Fake repo root copy so install can find scripts/ and mcp/ tree without
  # writing anywhere outside the sandbox. We only need files install touches.
  mkdir -p "$SANDBOX/repo/scripts"
  mkdir -p "$SANDBOX/repo/mcp/sample-server"
  cp "$REPO_ROOT/scripts/install.sh" "$SANDBOX/repo/scripts/install.sh"
  cp "$REPO_ROOT/scripts/_install_helpers.bash" "$SANDBOX/repo/scripts/_install_helpers.bash"
  printf 'requests>=2.0\n' >"$SANDBOX/repo/scripts/requirements.txt"
  printf '{"name":"sample-server","version":"0.0.1"}\n' \
    >"$SANDBOX/repo/mcp/sample-server/package.json"
  _write_mock_bins
}

harness_teardown() {
  if [[ -n "${SANDBOX:-}" && -d "$SANDBOX" ]]; then
    rm -rf "$SANDBOX"
  fi
  SANDBOX=""
}

harness_set_python() { MOCK_PYTHON_VERSION="$1"; _write_mock_bins; }
harness_set_node()   { MOCK_NODE_VERSION="$1";   _write_mock_bins; }
harness_set_npm_fails() { MOCK_NPM_FAILS="$1";   _write_mock_bins; }
harness_set_pip_fails() { MOCK_PIP_FAILS="$1";   _write_mock_bins; }

# Run the install script with $1.. forwarded. Optionally pipe stdin via
# HARNESS_STDIN env var (a string).
run_install() {
  local log_file
  log_file="$(mktemp -t aiswmm-install-log.XXXXXX)"
  local status=0
  if [[ -n "${HARNESS_STDIN:-}" ]]; then
    PATH="$SANDBOX/bin:/usr/bin:/bin" \
      HOME="$SANDBOX/home" \
      AISWMM_CONFIG_DIR="$SANDBOX/home/.aiswmm" \
      XDG_CACHE_HOME="$SANDBOX/cache" \
      AISWMM_SKIP_REAL_TOOLS=1 \
      bash "$SANDBOX/repo/scripts/install.sh" "$@" \
        <<<"$HARNESS_STDIN" >"$log_file" 2>&1 \
        || status=$?
  else
    PATH="$SANDBOX/bin:/usr/bin:/bin" \
      HOME="$SANDBOX/home" \
      AISWMM_CONFIG_DIR="$SANDBOX/home/.aiswmm" \
      XDG_CACHE_HOME="$SANDBOX/cache" \
      AISWMM_SKIP_REAL_TOOLS=1 \
      bash "$SANDBOX/repo/scripts/install.sh" "$@" \
        </dev/null >"$log_file" 2>&1 \
        || status=$?
  fi
  INSTALL_STATUS=$status
  INSTALL_LOG="$(cat "$log_file")"
  rm -f "$log_file"
}

# Convenience asserter for tests: fails fast on the first miss.
assert_log_contains() {
  local needle="$1"
  if [[ "$INSTALL_LOG" != *"$needle"* ]]; then
    printf 'FAIL: expected log to contain %q\n' "$needle" >&2
    printf -- '--- log ---\n%s\n--- end log ---\n' "$INSTALL_LOG" >&2
    return 1
  fi
}

assert_log_not_contains() {
  local needle="$1"
  if [[ "$INSTALL_LOG" == *"$needle"* ]]; then
    printf 'FAIL: did not expect log to contain %q\n' "$needle" >&2
    printf -- '--- log ---\n%s\n--- end log ---\n' "$INSTALL_LOG" >&2
    return 1
  fi
}

assert_status() {
  local expected="$1"
  if [[ "$INSTALL_STATUS" != "$expected" ]]; then
    printf 'FAIL: expected exit status %s, got %s\n' "$expected" "$INSTALL_STATUS" >&2
    printf -- '--- log ---\n%s\n--- end log ---\n' "$INSTALL_LOG" >&2
    return 1
  fi
}

# ---------------------------------------------------------------------------
# internal: write the fake binaries.
# ---------------------------------------------------------------------------
_write_mock_bins() {
  [[ -d "$SANDBOX/bin" ]] || return 0

  cat >"$SANDBOX/bin/python3" <<MOCKPY
#!/usr/bin/env bash
# Mock python3. Reports MOCK_PYTHON_VERSION and answers sys.version_info probes.
if [[ "\${1:-}" == "--version" ]]; then
  printf 'Python %s\n' "$MOCK_PYTHON_VERSION"
  exit 0
fi
# python3 -c "...version check..."
if [[ "\${1:-}" == "-c" ]]; then
  case "\$2" in
    *"sys.version_info"*">= (3, 10)"*)
      ver="$MOCK_PYTHON_VERSION"
      maj="\${ver%%.*}"
      min="\${ver#*.}"; min="\${min%%.*}"
      if [[ "\$maj" -gt 3 ]] || { [[ "\$maj" -eq 3 ]] && [[ "\$min" -ge 10 ]]; }; then
        exit 0
      fi
      exit 1
      ;;
  esac
fi
# Heredoc on stdin (version probe form used by install.sh).
if [[ "\${1:-}" == "-" ]]; then
  src="\$(cat)"
  if [[ "\$src" == *"sys.version_info"*">= (3, 10)"* ]]; then
    ver="$MOCK_PYTHON_VERSION"
    maj="\${ver%%.*}"
    min="\${ver#*.}"; min="\${min%%.*}"
    if [[ "\$maj" -gt 3 ]] || { [[ "\$maj" -eq 3 ]] && [[ "\$min" -ge 10 ]]; }; then
      exit 0
    fi
    exit 1
  fi
  exit 0
fi
# python3 -m venv DIR
if [[ "\${1:-}" == "-m" && "\${2:-}" == "venv" ]]; then
  mkdir -p "\${3}/bin"
  cp "\$0" "\${3}/bin/python"
  cat >"\${3}/bin/pip" <<'PIP'
#!/usr/bin/env bash
exit 0
PIP
  chmod +x "\${3}/bin/python" "\${3}/bin/pip"
  exit 0
fi
# python3 -m pip ...
if [[ "\${1:-}" == "-m" && "\${2:-}" == "pip" ]]; then
  if [[ "$MOCK_PIP_FAILS" == "1" ]]; then
    printf 'mock pip failure\n' >&2
    exit 7
  fi
  exit 0
fi
# python3 -m agentic_swmm.cli ...
if [[ "\${1:-}" == "-m" ]]; then
  exit 0
fi
exit 0
MOCKPY
  chmod +x "$SANDBOX/bin/python3"
  # python3.12 alias for resolve_python loop preference
  cp "$SANDBOX/bin/python3" "$SANDBOX/bin/python3.12"

  cat >"$SANDBOX/bin/node" <<MOCKNODE
#!/usr/bin/env bash
if [[ "\${1:-}" == "--version" || "\${1:-}" == "-v" ]]; then
  printf 'v%s.0.0\n' "$MOCK_NODE_VERSION"
  exit 0
fi
exit 0
MOCKNODE
  chmod +x "$SANDBOX/bin/node"

  cat >"$SANDBOX/bin/npm" <<MOCKNPM
#!/usr/bin/env bash
if [[ "\${1:-}" == "--version" || "\${1:-}" == "-v" ]]; then
  printf '10.0.0\n'
  exit 0
fi
if [[ "$MOCK_NPM_FAILS" == "1" ]]; then
  printf 'mock npm failure\n' >&2
  exit 9
fi
exit 0
MOCKNPM
  chmod +x "$SANDBOX/bin/npm"

  # Minimal stubs for tools install.sh might consult.
  for tool in git cmake brew; do
    cat >"$SANDBOX/bin/$tool" <<TOOL
#!/usr/bin/env bash
exit 0
TOOL
    chmod +x "$SANDBOX/bin/$tool"
  done

  # uname stub: pretend Linux so the macOS Homebrew branch isn't taken.
  cat >"$SANDBOX/bin/uname" <<'UNAME'
#!/usr/bin/env bash
if [[ "${1:-}" == "-s" ]]; then echo Linux; exit 0; fi
exec /usr/bin/uname "$@"
UNAME
  chmod +x "$SANDBOX/bin/uname"
}
