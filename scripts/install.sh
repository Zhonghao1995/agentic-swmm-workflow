#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

show_help() {
  cat <<'USAGE'
Usage: scripts/install.sh [--yes] [--skip-python] [--skip-mcp] [--skip-swmm] [--skip-setup] [--provider NAME] [--model MODEL] [--swmm-ref REF] [--help]

Bootstrap local dependencies for agentic-swmm-workflow.

Options:
  --yes          Run non-interactively.
  --skip-python  Skip virtualenv creation and pip installs.
  --skip-mcp     Skip npm installs for skills/*/scripts/mcp packages.
  --skip-swmm    Skip SWMM engine installation/build.
  --skip-setup   Skip aiswmm orchestration setup after installation.
  --provider NAME  Provider to register with aiswmm setup. Default: openai.
  --model MODEL  Model to register with aiswmm setup. Default: gpt-5.5.
  --swmm-ref REF  USEPA SWMM Git ref to build. Default: v5.2.4.
  --help         Show this help message.
USAGE
}

log() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

YES=0
SKIP_PYTHON=0
SKIP_MCP=0
SKIP_SWMM=0
SKIP_SETUP=0
AISWMM_PROVIDER="${AISWMM_PROVIDER:-openai}"
AISWMM_MODEL="${AISWMM_MODEL:-gpt-5.5}"
SWMM_REF="${SWMM_REF:-v5.2.4}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) YES=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    --skip-mcp) SKIP_MCP=1 ;;
    --skip-swmm) SKIP_SWMM=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    --provider)
      [[ $# -ge 2 ]] || fail "--provider requires a value"
      AISWMM_PROVIDER="$2"
      shift
      ;;
    --model)
      [[ $# -ge 2 ]] || fail "--model requires a value"
      AISWMM_MODEL="$2"
      shift
      ;;
    --swmm-ref)
      [[ $# -ge 2 ]] || fail "--swmm-ref requires a value"
      SWMM_REF="$2"
      shift
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/agentic-swmm-workflow"
SWMM_SAFE_REF="${SWMM_REF//\//_}"
SWMM_SRC_DIR="$CACHE_ROOT/swmm-src-$SWMM_SAFE_REF"
SWMM_BUILD_DIR="$CACHE_ROOT/swmm-build-$SWMM_SAFE_REF"
LOCAL_BIN_DIR="$HOME/.local/bin"

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux) echo "linux" ;;
    *)
      fail "Unsupported platform: $(uname -s). Use scripts/install.ps1 on Windows."
      ;;
  esac
}

PLATFORM="$(detect_platform)"

ensure_confirmation() {
  if [[ $YES -eq 1 ]]; then
    return
  fi
  printf "This will install dependencies for %s. Continue? [y/N] " "$REPO_ROOT"
  read -r reply || true
  case "${reply:-}" in
    y|Y|yes|YES) ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
}

ensure_homebrew() {
  if command -v brew >/dev/null 2>&1; then
    return
  fi
  log "Installing Homebrew"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
}

load_homebrew_env() {
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
    eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
  fi
}

require_sudo() {
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    echo ""
    return
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    fail "sudo is required to install system packages on this platform."
  fi
  echo "sudo"
}

install_system_packages_linux() {
  local sudo_cmd
  sudo_cmd="$(require_sudo)"
  if command -v apt-get >/dev/null 2>&1; then
    log "Installing Linux packages with apt-get"
    $sudo_cmd apt-get update
    $sudo_cmd apt-get install -y \
      build-essential \
      cmake \
      curl \
      git \
      nodejs \
      npm \
      python3 \
      python3-pip \
      python3-venv
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    log "Installing Linux packages with dnf"
    $sudo_cmd dnf install -y \
      cmake \
      curl \
      gcc \
      gcc-c++ \
      git \
      make \
      nodejs \
      npm \
      python3 \
      python3-pip
    return
  fi
  if command -v yum >/dev/null 2>&1; then
    log "Installing Linux packages with yum"
    $sudo_cmd yum install -y \
      cmake \
      curl \
      gcc \
      gcc-c++ \
      git \
      make \
      nodejs \
      npm \
      python3 \
      python3-pip
    return
  fi
  fail "No supported Linux package manager found. Install python3, node, npm, git, cmake, and a C compiler manually."
}

ensure_toolchain() {
  if [[ $PLATFORM == "macos" ]]; then
    ensure_homebrew
    load_homebrew_env
    log "Installing system packages with Homebrew"
    brew install cmake git node python
    return
  fi
  install_system_packages_linux
}

ensure_local_bin_on_path() {
  mkdir -p "$LOCAL_BIN_DIR"
  export PATH="$LOCAL_BIN_DIR:$PATH"
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    return
  fi
  ensure_toolchain
  command -v python3 >/dev/null 2>&1 || fail "python3 is still unavailable after installation."
}

ensure_node() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    return
  fi
  ensure_toolchain
  command -v node >/dev/null 2>&1 || fail "node is still unavailable after installation."
  command -v npm >/dev/null 2>&1 || fail "npm is still unavailable after installation."
}

ensure_build_tools_for_swmm() {
  if command -v git >/dev/null 2>&1 && command -v cmake >/dev/null 2>&1; then
    return
  fi
  ensure_toolchain
}

build_swmm_from_source() {
  ensure_build_tools_for_swmm
  ensure_local_bin_on_path
  mkdir -p "$CACHE_ROOT"

  if [[ ! -d "$SWMM_SRC_DIR/.git" ]]; then
    log "Cloning USEPA SWMM solver source at $SWMM_REF"
    git init "$SWMM_SRC_DIR"
    git -C "$SWMM_SRC_DIR" remote add origin https://github.com/USEPA/Stormwater-Management-Model.git
  else
    log "Updating cached USEPA SWMM solver source at $SWMM_REF"
  fi
  git -C "$SWMM_SRC_DIR" fetch --depth 1 origin "$SWMM_REF"
  git -C "$SWMM_SRC_DIR" checkout --detach FETCH_HEAD

  log "Building SWMM solver from source ($SWMM_REF)"
  cmake -S "$SWMM_SRC_DIR" -B "$SWMM_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$SWMM_BUILD_DIR" --config Release -j "${SWMM_BUILD_JOBS:-4}"

  local runswmm
  runswmm="$(
    find "$SWMM_BUILD_DIR" -type f \( -name 'runswmm' -o -name 'runswmm.exe' \) | head -n 1
  )"
  [[ -n "$runswmm" ]] || fail "Unable to locate built SWMM executable in $SWMM_BUILD_DIR"

  cp "$runswmm" "$LOCAL_BIN_DIR/$(basename "$runswmm")"
  if [[ "$runswmm" == *.exe ]]; then
    cat >"$LOCAL_BIN_DIR/swmm5.cmd" <<EOF
@echo off
"$LOCAL_BIN_DIR\\$(basename "$runswmm")" %*
EOF
  else
    ln -sf "$LOCAL_BIN_DIR/$(basename "$runswmm")" "$LOCAL_BIN_DIR/swmm5"
  fi
}

ensure_swmm() {
  if [[ $SKIP_SWMM -eq 1 ]]; then
    return
  fi
  ensure_local_bin_on_path
  if command -v swmm5 >/dev/null 2>&1; then
    return
  fi
  build_swmm_from_source
  command -v swmm5 >/dev/null 2>&1 || fail "SWMM install completed, but swmm5 is still not on PATH."
}

install_python_requirements() {
  ensure_python
  [[ -f "$REQ_FILE" ]] || fail "Missing requirements file: $REQ_FILE"

  log "Creating virtualenv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"

  log "Installing Python dependencies"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$REQ_FILE"
  "$VENV_DIR/bin/python" -m pip install -e "$REPO_ROOT"
}

install_mcp_requirements() {
  ensure_node
  local mcp_count=0
  while IFS= read -r package_json; do
    local mcp_dir
    mcp_dir="$(dirname "$package_json")"
    mcp_count=$((mcp_count + 1))
    log "Installing MCP deps in $mcp_dir"
    if [[ -f "$mcp_dir/package-lock.json" ]]; then
      (cd "$mcp_dir" && npm ci)
    else
      (cd "$mcp_dir" && npm install)
    fi
  done < <(find "$REPO_ROOT/skills" -type f -path '*/scripts/mcp/package.json' | sort)
  log "Installed MCP deps in $mcp_count package(s)"
}

run_aiswmm_setup() {
  if [[ $SKIP_SETUP -eq 1 || $SKIP_PYTHON -eq 1 ]]; then
    return
  fi
  log "Configuring Agentic SWMM orchestration layer"
  "$VENV_DIR/bin/python" -m agentic_swmm.cli setup --provider "$AISWMM_PROVIDER" --model "$AISWMM_MODEL"
}

swmm_status() {
  ensure_local_bin_on_path
  if command -v swmm5 >/dev/null 2>&1; then
    local swmm_bin swmm_ver
    swmm_bin="$(command -v swmm5)"
    swmm_ver="$(swmm5 --version 2>/dev/null | head -n 1 || true)"
    if [[ -n "$swmm_ver" ]]; then
      printf 'found at %s (%s)' "$swmm_bin" "$swmm_ver"
    else
      printf 'found at %s' "$swmm_bin"
    fi
  else
    printf 'missing'
  fi
}

ensure_confirmation

if [[ $SKIP_PYTHON -eq 0 ]]; then
  install_python_requirements
fi

if [[ $SKIP_MCP -eq 0 ]]; then
  install_mcp_requirements
fi

ensure_swmm
run_aiswmm_setup

cat <<SUMMARY

Install summary
- Repo root: $REPO_ROOT
- Python setup: $([[ $SKIP_PYTHON -eq 0 ]] && echo "installed (.venv + scripts/requirements.txt + agentic-swmm CLI)" || echo "skipped (--skip-python)")
- MCP npm setup: $([[ $SKIP_MCP -eq 0 ]] && echo "installed" || echo "skipped (--skip-mcp)")
- Agentic SWMM setup: $([[ $SKIP_SETUP -eq 0 && $SKIP_PYTHON -eq 0 ]] && echo "registered provider=$AISWMM_PROVIDER model=$AISWMM_MODEL skills/MCP/memory" || echo "skipped")
- SWMM ref: $SWMM_REF
- SWMM check: $(swmm_status)

Next steps
1. Activate the virtualenv: source .venv/bin/activate
2. Set an OpenAI key for real chat: export OPENAI_API_KEY="..."
3. Check the CLI: aiswmm doctor
4. Start local orchestration chat: aiswmm chat --provider $AISWMM_PROVIDER "Explain what this Agentic SWMM installation can do"
5. Run acceptance: aiswmm demo acceptance --run-id latest
6. Open report: runs/acceptance/latest/acceptance_report.md
SUMMARY
