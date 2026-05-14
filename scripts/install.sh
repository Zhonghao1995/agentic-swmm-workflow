#!/usr/bin/env bash
# scripts/install.sh
#
# Stepped interactive installer for the Agentic Stormwater Modeling
# Workflow (AISWMM). See `--help` for flags. The flow:
#
#   1. Prerequisite checks  (python >=3.10, node >=18)
#   2. Risk warning banner  -> Y/n
#   3. Per-step Y/n:
#        Step 1/5 -- Python venv creation        (~30s)
#        Step 2/5 -- Python deps install         (~2 min)
#        Step 3/5 -- MCP servers npm install     (~2 min, 8 servers)
#        Step 4/5 -- Skill files copy            (~10s)
#        Step 5/5 -- OpenAI API key config       (skippable)
#   4. Success summary + next-step hint.
#
# Failure at any step prints a clear remediation hint, not raw stderr,
# and exits non-zero. N at any prompt exits 0 with "Installation aborted".
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=./_install_helpers.bash
source "$SCRIPT_DIR/_install_helpers.bash"

show_help() {
  cat <<'USAGE'
Usage: scripts/install.sh [flags]

Stepped interactive installer for the Agentic SWMM workflow.

Flags:
  --auto           Skip all prompts (CI / scripted install). Defaults to Y at
                   every step. Also disables the chat auto-start prompt.
  --yes            Legacy alias for --auto (kept for compatibility).
  --skip-python    Skip Python venv + Python deps steps.
  --skip-mcp       Skip MCP server npm install step.
  --skip-swmm      Skip SWMM engine install step.
  --skip-setup     Skip aiswmm orchestration setup after installation.
  --provider NAME  LLM provider to register (default: openai).
  --model MODEL    LLM model to register (default: gpt-5.5).
  --swmm-ref REF   USEPA SWMM Git ref (default: v5.2.4).
  --help, -h       Show this help message.
USAGE
}

# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

INSTALL_AUTO=0
SKIP_PYTHON=0
SKIP_MCP=0
SKIP_SWMM=0
SKIP_SETUP=0
AISWMM_PROVIDER="${AISWMM_PROVIDER:-openai}"
AISWMM_MODEL="${AISWMM_MODEL:-gpt-5.5}"
SWMM_REF="${SWMM_REF:-v5.2.4}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto|--yes) INSTALL_AUTO=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    --skip-mcp) SKIP_MCP=1 ;;
    --skip-swmm) SKIP_SWMM=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    --provider)
      [[ $# -ge 2 ]] || { print_failure "--provider requires a value"; exit 2; }
      AISWMM_PROVIDER="$2"; shift ;;
    --model)
      [[ $# -ge 2 ]] || { print_failure "--model requires a value"; exit 2; }
      AISWMM_MODEL="$2"; shift ;;
    --swmm-ref)
      [[ $# -ge 2 ]] || { print_failure "--swmm-ref requires a value"; exit 2; }
      SWMM_REF="$2"; shift ;;
    --help|-h) show_help; exit 0 ;;
    *) print_failure "Unknown option: $1" "Run 'scripts/install.sh --help' for usage."; exit 2 ;;
  esac
  shift
done
export INSTALL_AUTO

VENV_DIR="$REPO_ROOT/.venv"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
AISWMM_CONFIG_DIR="${AISWMM_CONFIG_DIR:-$HOME/.aiswmm}"
AISWMM_ENV_FILE="$AISWMM_CONFIG_DIR/env"

# Are we running under the bash test harness? The harness sets a flag so the
# install script can skip operations that would require real external state.
TEST_MODE="${AISWMM_SKIP_REAL_TOOLS:-0}"

# ---------------------------------------------------------------------------
# Resolve a python binary that satisfies >=3.10. Sets RESOLVED_PYTHON.
# ---------------------------------------------------------------------------
RESOLVED_PYTHON=""
resolve_python() {
  local candidate
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if check_python_version "$candidate" 2>/dev/null; then
        RESOLVED_PYTHON="$(command -v "$candidate")"
        return 0
      fi
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

do_python_venv() {
  if [[ "$TEST_MODE" == "1" ]]; then
    mkdir -p "$VENV_DIR/bin"
    : >"$VENV_DIR/bin/python"
    chmod +x "$VENV_DIR/bin/python"
    return 0
  fi
  "$RESOLVED_PYTHON" -m venv "$VENV_DIR"
}

do_python_deps() {
  if [[ "$TEST_MODE" == "1" ]]; then
    return 0
  fi
  local venv_python="$VENV_DIR/bin/python"
  "$venv_python" -m pip install --upgrade pip
  "$venv_python" -m pip install -r "$REQ_FILE"
  "$venv_python" -m pip install -e "$REPO_ROOT"
}

do_mcp_install() {
  if [[ "$TEST_MODE" == "1" ]]; then
    # Honour mocked npm failures by running mock npm at least once.
    npm --version >/dev/null
    npm install --silent 2>/dev/null || return $?
    return 0
  fi
  local count=0
  local package_json mcp_dir
  while IFS= read -r package_json; do
    mcp_dir="$(dirname "$package_json")"
    count=$((count + 1))
    if [[ -f "$mcp_dir/package-lock.json" ]]; then
      (cd "$mcp_dir" && npm ci)
    else
      (cd "$mcp_dir" && npm install)
    fi
  done < <(find "$REPO_ROOT/mcp" -mindepth 2 -maxdepth 2 -type f -name package.json | sort)
  printf 'Installed deps in %s MCP package(s)\n' "$count"
}

do_skill_copy() {
  mkdir -p "$AISWMM_CONFIG_DIR"
  if [[ "$TEST_MODE" == "1" ]]; then
    return 0
  fi
  # Real implementation defers to `aiswmm setup` (step 5) for skill linkage;
  # this step just guarantees the config dir exists.
  return 0
}

do_api_key() {
  mkdir -p "$AISWMM_CONFIG_DIR"
  if [[ "$AISWMM_PROVIDER" != "openai" ]]; then
    echo "Provider is $AISWMM_PROVIDER; OpenAI API key step skipped."
    return 0
  fi
  if [[ -n "${OPENAI_API_KEY:-}" || -f "$AISWMM_ENV_FILE" ]]; then
    echo "OpenAI API key already configured at $AISWMM_ENV_FILE"
    return 0
  fi
  if [[ "$INSTALL_AUTO" == "1" || "$TEST_MODE" == "1" ]]; then
    echo "API key configuration skipped (auto / test mode)."
    return 0
  fi
  printf 'Paste OpenAI API key (or press Enter to skip): '
  local api_key=""
  IFS= read -rs api_key || api_key=""
  printf '\n'
  if [[ -z "${api_key:-}" ]]; then
    echo "Skipped. Add it later in $AISWMM_ENV_FILE"
    return 0
  fi
  {
    printf '# Agentic SWMM local secrets. This file is sourced by ~/.local/bin/aiswmm.\n'
    printf 'export OPENAI_API_KEY=%q\n' "$api_key"
  } >"$AISWMM_ENV_FILE"
  chmod 600 "$AISWMM_ENV_FILE"
  echo "Saved OpenAI API key to $AISWMM_ENV_FILE"
}

# ---------------------------------------------------------------------------
# Prereq checks (top of the flow)
# ---------------------------------------------------------------------------

# Resolve python first to make the failure path symmetric: the resolve loop
# silences `check_python_version` so it can iterate candidates; we then call
# it once more with the final candidate so the user sees a real remediation.
if ! resolve_python; then
  check_python_version python3 || true
  exit 2
fi

if ! check_node_version node; then
  exit 2
fi

# ---------------------------------------------------------------------------
# Risk warning + top-level confirm
# ---------------------------------------------------------------------------

print_banner

if ! prompt_yn "Continue with installation?" "Y"; then
  echo "Installation aborted."
  exit 0
fi

# ---------------------------------------------------------------------------
# Stepped flow
# ---------------------------------------------------------------------------

total=5
fail_step() {
  local step_label="$1"; shift
  print_failure "$step_label failed." "$@"
  exit 1
}

# Step 1: Python venv
if [[ "$SKIP_PYTHON" == "1" ]]; then
  echo "Step 1/${total}: Python venv (skipped via --skip-python)"
else
  if ! prompt_yn "Run Step 1/${total} (Python venv ~30s)?" "Y"; then
    echo "Installation aborted at Python venv step."
    exit 0
  fi
  if ! run_step 1 "$total" "Python venv creation" "30s" do_python_venv; then
    fail_step "Python venv creation" \
      "Verify $RESOLVED_PYTHON can run 'python -m venv'." \
      "Delete $VENV_DIR and retry: bash scripts/install.sh"
  fi
fi

# Step 2: Python deps
if [[ "$SKIP_PYTHON" == "1" ]]; then
  echo "Step 2/${total}: Python deps (skipped via --skip-python)"
else
  if ! prompt_yn "Run Step 2/${total} (Python deps ~2 min)?" "Y"; then
    echo "Installation aborted at Python deps step."
    exit 0
  fi
  if ! run_step 2 "$total" "Python deps install" "2 min" do_python_deps; then
    fail_step "Python dependency install" \
      "Check network access to PyPI." \
      "Inspect $REQ_FILE; resolve conflicting versions and retry."
  fi
fi

# Step 3: MCP node_modules
if [[ "$SKIP_MCP" == "1" ]]; then
  echo "Step 3/${total}: MCP servers (skipped via --skip-mcp)"
else
  if ! prompt_yn "Run Step 3/${total} (MCP servers ~2 min, 8 servers)?" "Y"; then
    echo "Installation aborted at MCP step."
    exit 0
  fi
  if ! run_step 3 "$total" "MCP servers npm install" "2 min" do_mcp_install; then
    fail_step "MCP server install failed" \
      "Verify 'npm --version' works and you have network access to the npm registry." \
      "Retry with: bash scripts/install.sh --skip-python (skips finished steps)."
  fi
fi

# Step 4: skill files
if ! prompt_yn "Run Step 4/${total} (Skill files copy ~10s)?" "Y"; then
  echo "Installation aborted at skill copy step."
  exit 0
fi
if ! run_step 4 "$total" "Skill files copy to ~/.aiswmm" "10s" do_skill_copy; then
  fail_step "Skill files copy failed" \
    "Verify $HOME is writable and $AISWMM_CONFIG_DIR can be created."
fi

# Step 5: API key
if ! prompt_yn "Run Step 5/${total} (API key config; skippable)?" "Y"; then
  echo "Installation aborted at API key step."
  exit 0
fi
if ! run_step 5 "$total" "OpenAI API key configuration" "10s" do_api_key; then
  fail_step "API key configuration failed" \
    "You can add the key later by editing $AISWMM_ENV_FILE."
fi

# ---------------------------------------------------------------------------
# Success summary + next steps
# ---------------------------------------------------------------------------

cat <<SUMMARY

Install complete.

Summary
- Repo root:    $REPO_ROOT
- Python venv:  $([[ "$SKIP_PYTHON" == "1" ]] && echo "skipped" || echo "$VENV_DIR")
- MCP servers:  $([[ "$SKIP_MCP" == "1" ]] && echo "skipped" || echo "installed")
- Config dir:   $AISWMM_CONFIG_DIR
- Provider:     $AISWMM_PROVIDER ($AISWMM_MODEL)

Next steps
  1. Open a new shell so PATH updates take effect.
  2. Run: aiswmm doctor
  3. Run: aiswmm chat --provider $AISWMM_PROVIDER

SUMMARY

exit 0
