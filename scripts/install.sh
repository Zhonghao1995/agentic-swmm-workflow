#!/usr/bin/env bash
# scripts/install.sh
#
# Stepped interactive installer for the Agentic Stormwater Modeling
# Workflow (AISWMM). See `--help` for flags. The flow:
#
#   1. Prerequisite checks  (python >=3.10, node >=18)
#   2. Risk warning banner  -> Y/n
#   3. Per-step Y/n:
#        Step 1/6 -- Python venv creation        (~30s)
#        Step 2/6 -- Python deps install         (~2 min)
#        Step 3/6 -- MCP servers npm install     (~2 min, ~11 servers)
#        Step 4/6 -- Initialize ~/.aiswmm/        (~5s)
#        Step 5/6 -- OpenAI API key config       (skippable)
#        Step 6/6 -- Build SWMM 5.2.4 engine      (~2 min, skippable, non-fatal)
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
  --skip-swmm      Skip building the local SWMM 5.2.4 solver engine.
  --provider NAME  LLM provider to register (default: openai).
  --model MODEL    LLM model to register (default: gpt-5.5).
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
AISWMM_PROVIDER="${AISWMM_PROVIDER:-openai}"
AISWMM_MODEL="${AISWMM_MODEL:-gpt-5.5}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto|--yes) INSTALL_AUTO=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    --skip-mcp) SKIP_MCP=1 ;;
    --skip-swmm) SKIP_SWMM=1 ;;
    --provider)
      [[ $# -ge 2 ]] || { print_failure "--provider requires a value"; exit 2; }
      AISWMM_PROVIDER="$2"; shift ;;
    --model)
      [[ $# -ge 2 ]] || { print_failure "--model requires a value"; exit 2; }
      AISWMM_MODEL="$2"; shift ;;
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
# Pinned EPA SWMM engine (matches Dockerfile SWMM_REF + the runner's
# EXPECTED_SWMM_VERSION). Built from source into $AISWMM_CONFIG_DIR/swmm/.
SWMM_VERSION="5.2.4"

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
    # Only OpenAI uses the interactive prompt below; other providers are
    # pointed at `aiswmm login` from the always-visible Next steps block
    # (run_step hides this step's stdout on success).
    echo "Provider is $AISWMM_PROVIDER; the OpenAI key step does not apply."
    return 0
  fi
  if [[ -n "${OPENAI_API_KEY:-}" || -f "$AISWMM_ENV_FILE" ]]; then
    echo "OpenAI API key already configured at $AISWMM_ENV_FILE"
    return 0
  fi
  if [[ "$TEST_MODE" == "1" ]]; then
    echo "API key configuration skipped (test mode)."
    return 0
  fi
  # In curl|bash flow stdin is the curl pipe, not a terminal, so we read/write
  # via /dev/tty. If /dev/tty is unavailable (CI), fall back to the skip path.
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    echo "No interactive terminal available; skipping API key prompt."
    echo "Add it later by editing $AISWMM_ENV_FILE"
    return 0
  fi
  printf 'Paste OpenAI API key (or press Enter to skip): ' >/dev/tty
  local api_key=""
  IFS= read -rs api_key </dev/tty || api_key=""
  printf '\n' >/dev/tty
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

do_swmm_engine() {
  # Build the pinned EPA SWMM solver from source into $AISWMM_CONFIG_DIR/swmm/,
  # mirroring the Dockerfile so a run uses the same 5.2.4 engine. A co-located
  # `swmm5` wrapper sets the dynamic-lib path so runswmm finds libswmm5 wherever
  # it is moved, and resolve_swmm5()/doctor look in this fixed directory.
  local swmm_dir="$AISWMM_CONFIG_DIR/swmm"
  local wrapper="$swmm_dir/swmm5"
  mkdir -p "$swmm_dir"
  # Idempotent: skip when a working pinned engine is already present.
  if [[ -x "$wrapper" ]] && "$wrapper" --version 2>/dev/null | grep -q "$SWMM_VERSION"; then
    echo "swmm5 $SWMM_VERSION already installed at $wrapper"
    return 0
  fi
  if [[ "$TEST_MODE" == "1" ]]; then
    echo "SWMM engine build skipped (test mode)."
    return 0
  fi
  command -v git >/dev/null 2>&1   || { echo "git not found (needed to fetch SWMM source)."; return 1; }
  command -v cmake >/dev/null 2>&1 || { echo "cmake not found (needed to build swmm5). Install cmake and re-run."; return 1; }
  command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || { echo "no C compiler found (install Xcode CLT on macOS or build-essential on Linux)."; return 1; }

  local src build os runswmm
  os="$(uname -s)"
  src="$(mktemp -d)"; build="$src/build"
  if ! git clone --depth 1 --branch "v$SWMM_VERSION" \
      https://github.com/USEPA/Stormwater-Management-Model.git "$src/swmm"; then
    rm -rf "$src"; echo "failed to clone USEPA SWMM v$SWMM_VERSION."; return 1
  fi

  local -a cmake_args=(-S "$src/swmm" -B "$build" -DCMAKE_BUILD_TYPE=Release)
  if [[ "$os" == "Darwin" ]]; then
    # Apple clang ships no OpenMP runtime; build + link against Homebrew libomp.
    if ! command -v brew >/dev/null 2>&1; then
      rm -rf "$src"; echo "Homebrew required to supply libomp on macOS (https://brew.sh)."; return 1
    fi
    brew list libomp >/dev/null 2>&1 || brew install libomp || { rm -rf "$src"; echo "libomp install failed."; return 1; }
    local libomp; libomp="$(brew --prefix libomp)"
    cmake_args+=(
      -DOpenMP_C_FLAGS="-Xpreprocessor -fopenmp -I$libomp/include"
      -DOpenMP_C_LIB_NAMES=omp
      -DOpenMP_omp_LIBRARY="$libomp/lib/libomp.dylib"
      -DOpenMP_CXX_FLAGS="-Xpreprocessor -fopenmp -I$libomp/include"
      -DOpenMP_CXX_LIB_NAMES=omp
    )
  fi
  if ! cmake "${cmake_args[@]}"; then rm -rf "$src"; echo "cmake configure failed."; return 1; fi
  if ! cmake --build "$build" --config Release -j "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"; then
    rm -rf "$src"; echo "swmm5 build failed."; return 1
  fi

  runswmm="$(find "$build" -type f -name runswmm -print -quit)"
  if [[ -z "$runswmm" ]]; then rm -rf "$src"; echo "build produced no runswmm binary."; return 1; fi
  cp "$runswmm" "$swmm_dir/runswmm"
  find "$build" -type f \( -name 'libswmm5.*' -o -name 'libswmm-output.*' \) -exec cp {} "$swmm_dir/" \; 2>/dev/null || true
  # Wrapper: prepend its own dir to the dynamic-lib search path, then exec the
  # real binary, so the co-located libs resolve regardless of rpath/PATH.
  cat >"$wrapper" <<'WRAP'
#!/usr/bin/env bash
here="$(cd "$(dirname "$0")" && pwd)"
export DYLD_LIBRARY_PATH="$here:${DYLD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$here:${LD_LIBRARY_PATH:-}"
exec "$here/runswmm" "$@"
WRAP
  chmod +x "$wrapper" "$swmm_dir/runswmm"
  rm -rf "$src"
  if "$wrapper" --version 2>/dev/null | grep -q "$SWMM_VERSION"; then
    echo "Built swmm5 $SWMM_VERSION -> $wrapper"
    return 0
  fi
  echo "swmm5 built but did not report version $SWMM_VERSION."
  return 1
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

total=6
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
  if ! prompt_yn "Run Step 3/${total} (MCP servers ~2 min, ~11 servers)?" "Y"; then
    echo "Installation aborted at MCP step."
    exit 0
  fi
  if ! run_step 3 "$total" "MCP servers npm install" "2 min" do_mcp_install; then
    fail_step "MCP server install failed" \
      "Verify 'npm --version' works and you have network access to the npm registry." \
      "Retry with: bash scripts/install.sh --skip-python (skips finished steps)."
  fi
fi

# Step 4: initialize ~/.aiswmm/ (real skill deployment runs via `aiswmm setup`)
if ! prompt_yn "Run Step 4/${total} (Initialize ~/.aiswmm/ ~5s)?" "Y"; then
  echo "Installation aborted at config-dir init step."
  exit 0
fi
if ! run_step 4 "$total" "Initialize ~/.aiswmm/ directory" "5s" do_skill_copy; then
  fail_step "~/.aiswmm/ initialization failed" \
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

# Step 6: SWMM solver engine (NON-FATAL). The rest of the install stays usable
# even if the build fails (no compiler, no libomp, offline); `aiswmm doctor`
# reports a missing engine and how to fix it, so we warn and continue here.
if [[ "$SKIP_SWMM" == "1" ]]; then
  echo "Step 6/${total}: SWMM engine (skipped via --skip-swmm)"
elif ! prompt_yn "Run Step 6/${total} (Build SWMM $SWMM_VERSION engine ~2 min; skippable)?" "Y"; then
  echo "Skipped SWMM engine build. Install swmm5 yourself or re-run later; 'aiswmm doctor' confirms status."
else
  if ! run_step 6 "$total" "SWMM $SWMM_VERSION engine build" "2 min" do_swmm_engine; then
    print_failure "SWMM engine build failed (non-fatal)." \
      "The rest of the install is fine; this only affects running models locally." \
      "Re-run the installer, or build/obtain swmm5 $SWMM_VERSION yourself; 'aiswmm doctor' shows status." \
      "macOS note: the build needs Homebrew 'libomp' (brew install libomp)."
  fi
fi

# ---------------------------------------------------------------------------
# Success summary + next steps
# ---------------------------------------------------------------------------

swmm5_bin="$AISWMM_CONFIG_DIR/swmm/swmm5"
if [[ "$SKIP_SWMM" == "1" ]]; then
  swmm_status="skipped"
elif [[ -x "$swmm5_bin" ]] && "$swmm5_bin" --version 2>/dev/null | grep -q "$SWMM_VERSION"; then
  swmm_status="installed ($SWMM_VERSION)"
else
  swmm_status="not installed (run 'aiswmm doctor')"
fi

cat <<SUMMARY

Install complete.

Summary
- Repo root:    $REPO_ROOT
- Python venv:  $([[ "$SKIP_PYTHON" == "1" ]] && echo "skipped" || echo "$VENV_DIR")
- MCP servers:  $([[ "$SKIP_MCP" == "1" ]] && echo "skipped" || echo "installed")
- SWMM engine:  $swmm_status
- Config dir:   $AISWMM_CONFIG_DIR
- Provider:     $AISWMM_PROVIDER ($AISWMM_MODEL)

Next steps
  1. Open a new shell so PATH updates take effect.
  2. Run: aiswmm doctor
SUMMARY

# Provider key guidance lives here, not in Step 5: run_step hides a step's
# output on success, so a login hint inside the step would never be seen. We
# re-derive the key state at summary time (the same condition do_api_key uses):
#   - non-openai: the installer never prompts for a key -> always point at login
#   - openai with no key saved or pre-existing (user pressed Enter to skip, or
#     no tty was available) -> tell them how to add it
#   - openai with a key already configured -> just start chatting
if [[ "$AISWMM_PROVIDER" != "openai" ]]; then
  echo "  3. Store your $AISWMM_PROVIDER API key: aiswmm login --$AISWMM_PROVIDER"
  echo "  4. Run: aiswmm chat --provider $AISWMM_PROVIDER"
elif [[ -z "${OPENAI_API_KEY:-}" && ! -f "$AISWMM_ENV_FILE" ]]; then
  echo "  3. Add your OpenAI API key: aiswmm login --openai"
  echo "  4. Run: aiswmm chat --provider openai"
else
  echo "  3. Run: aiswmm chat --provider openai"
fi
echo ""

exit 0
