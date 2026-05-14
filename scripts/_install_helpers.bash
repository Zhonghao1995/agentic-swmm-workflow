#!/usr/bin/env bash
# scripts/_install_helpers.bash
#
# Shared helpers for the stepped install flow (scripts/install.sh).
# Sourced — not executed directly.
#
# Contracts:
#   - check_python_version: prints remediation + returns 1 if python <3.10
#   - check_node_version:   prints remediation + returns 1 if node <18
#   - prompt_yn:            asks Y/n; returns 0 for yes, 1 for no
#   - run_step:             prints "Step N/M: …", runs cmd, prints PASS/FAIL
#   - print_banner:         draws the welcome / risk-warning box

# Colour helpers — degrade gracefully when stdout is not a TTY.
if [[ -t 1 ]]; then
  C_DIM=$'\033[2m'
  C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'
  C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'
  C_RESET=$'\033[0m'
else
  C_DIM=""; C_BOLD=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_RESET=""
fi

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

# check_python_version PYTHON_BIN
# Returns 0 if PYTHON_BIN is python >=3.10, else prints a remediation block
# and returns 1. Does NOT exit — caller decides.
check_python_version() {
  local py="${1:-python3}"
  if ! command -v "$py" >/dev/null 2>&1; then
    cat >&2 <<EOF
${C_RED}[ERROR] Python 3.10+ not found on PATH.${C_RESET}

Remediation:
  - macOS:  brew install python@3.12
  - Linux:  sudo apt install python3.12   (or your distro's equivalent)
  - Then re-run: bash scripts/install.sh
EOF
    return 1
  fi
  local ok=1
  "$py" - <<'PY' >/dev/null 2>&1 && ok=0 || ok=1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  if [[ "$ok" -ne 0 ]]; then
    local version
    version="$("$py" --version 2>&1 || true)"
    cat >&2 <<EOF
${C_RED}[ERROR] Python 3.10+ required, found: ${version}${C_RESET}

Remediation:
  - macOS:  brew install python@3.12
  - Linux:  sudo apt install python3.12   (or your distro's equivalent)
  - Then re-run: bash scripts/install.sh
EOF
    return 1
  fi
  return 0
}

# check_node_version NODE_BIN
# Returns 0 if NODE_BIN is node >=18, else prints remediation and returns 1.
check_node_version() {
  local node_bin="${1:-node}"
  if ! command -v "$node_bin" >/dev/null 2>&1; then
    cat >&2 <<EOF
${C_RED}[ERROR] Node 18+ required for MCP servers, but 'node' is not on PATH.${C_RESET}

Remediation:
  - macOS:  brew install node
  - Linux:  sudo apt install nodejs npm    (or use nvm for newer versions)
  - Then re-run: bash scripts/install.sh
EOF
    return 1
  fi
  local raw major
  raw="$("$node_bin" --version 2>/dev/null || true)"
  # Strip leading "v" and split on first dot.
  raw="${raw#v}"
  major="${raw%%.*}"
  if ! [[ "$major" =~ ^[0-9]+$ ]] || [[ "$major" -lt 18 ]]; then
    cat >&2 <<EOF
${C_RED}[ERROR] Node 18+ required for MCP servers, found: v${raw}${C_RESET}

Remediation:
  - macOS:  brew install node
  - Linux:  sudo apt install nodejs npm    (or use nvm for newer versions)
  - Then re-run: bash scripts/install.sh
EOF
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# prompt_yn QUESTION [DEFAULT]
# DEFAULT is "Y" (default) or "N". Returns 0 for yes, 1 for no.
# When INSTALL_AUTO=1 the prompt is skipped and the default ("Y" if unset)
# is used. Reads from stdin so tests can pipe answers in.
prompt_yn() {
  local question="$1"
  local default="${2:-Y}"
  if [[ "${INSTALL_AUTO:-0}" == "1" ]]; then
    case "$default" in
      Y|y) return 0 ;;
      *)   return 1 ;;
    esac
  fi
  local suffix
  case "$default" in
    Y|y) suffix="[Y/n]" ;;
    *)   suffix="[y/N]" ;;
  esac
  printf '%s %s ' "$question" "$suffix"
  local reply=""
  IFS= read -r reply || reply=""
  if [[ -z "$reply" ]]; then
    reply="$default"
  fi
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

# run_step STEP_NUM TOTAL LABEL ESTIMATE CMD [ARGS...]
# Prints a single-line "Step N/M: LABEL (~estimate)" header, runs the
# command, then prints a PASS/FAIL footer with elapsed seconds.
# Returns the command's exit status. Output of CMD is captured and only
# shown when the command fails (so the happy path stays clean).
run_step() {
  local step="$1"; shift
  local total="$1"; shift
  local label="$1"; shift
  local estimate="$1"; shift

  printf '%sStep %s/%s:%s %s %s(~%s)%s\n' \
    "$C_BOLD" "$step" "$total" "$C_RESET" "$label" "$C_DIM" "$estimate" "$C_RESET"

  local start end elapsed log_file status=0
  start=$(date +%s)
  log_file="$(mktemp -t aiswmm-step.XXXXXX)"
  "$@" >"$log_file" 2>&1 || status=$?
  end=$(date +%s)
  elapsed=$((end - start))

  if [[ "$status" -eq 0 ]]; then
    printf '  %s[PASS]%s %s (%ss)\n' "$C_GREEN" "$C_RESET" "$label" "$elapsed"
  else
    printf '  %s[FAIL]%s %s (%ss)\n' "$C_RED" "$C_RESET" "$label" "$elapsed"
    printf '%s\n' "----- command output -----"
    cat "$log_file"
    printf '%s\n' "--------------------------"
  fi
  rm -f "$log_file"
  return $status
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

print_banner() {
  cat <<'BANNER'
+---------------------------------------------------+
|  AISWMM Installer                                 |
|  Agentic Stormwater Modeling Workflow             |
|                                                   |
|  This installer will:                             |
|  - Create a Python virtualenv (~50 MB)            |
|  - Install Python deps (~150 MB)                  |
|  - Install MCP servers via npm (~400 MB)          |
|  - Copy skill files to ~/.aiswmm/                 |
|  - Optionally configure your OpenAI API key       |
|                                                   |
|  Estimated total time: 3-5 minutes                |
|  Total disk: ~600 MB                              |
+---------------------------------------------------+
BANNER
}

# print_failure HUMAN_MESSAGE [REMEDIATION...]
# Renders a uniform multi-line failure block to stderr.
print_failure() {
  local headline="$1"; shift
  printf '\n%s[ERROR] %s%s\n' "$C_RED" "$headline" "$C_RESET" >&2
  if [[ $# -gt 0 ]]; then
    printf 'Remediation:\n' >&2
    local line
    for line in "$@"; do
      printf '  - %s\n' "$line" >&2
    done
  fi
}
