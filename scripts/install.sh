#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

show_help() {
  cat <<'USAGE'
Usage: scripts/install.sh [--yes] [--skip-python] [--skip-mcp] [--help]

Bootstrap local dependencies for agentic-swmm-workflow.

Options:
  --yes          Run non-interactively (skip confirmation prompt).
  --skip-python  Skip Python virtualenv creation and pip installs.
  --skip-mcp     Skip npm installs for skills/*/scripts/mcp packages.
  --help         Show this help message.
USAGE
}

YES=0
SKIP_PYTHON=0
SKIP_MCP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      YES=1
      ;;
    --skip-python)
      SKIP_PYTHON=1
      ;;
    --skip-mcp)
      SKIP_MCP=1
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      show_help
      exit 1
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

if [[ $YES -eq 0 ]]; then
  printf "This will install local dependencies in %s. Continue? [y/N] " "$REPO_ROOT"
  read -r reply || true
  case "${reply:-}" in
    y|Y|yes|YES)
      ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
fi

python_status="skipped (--skip-python)"
mcp_status="skipped (--skip-mcp)"

if [[ $SKIP_PYTHON -eq 0 ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 is required for quickstart but was not found on PATH." >&2
    echo "Install Python 3, then rerun scripts/install.sh." >&2
    exit 1
  fi

  if [[ ! -f "$REQ_FILE" ]]; then
    echo "[ERROR] Missing requirements file: $REQ_FILE" >&2
    exit 1
  fi

  echo "[INFO] Creating virtualenv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"

  echo "[INFO] Installing Python dependencies from $REQ_FILE"
  "$VENV_DIR/bin/pip" install --upgrade pip
  "$VENV_DIR/bin/pip" install -r "$REQ_FILE"

  python_status="installed (.venv + scripts/requirements.txt)"
fi

if [[ $SKIP_MCP -eq 0 ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "[WARN] npm not found; skipping MCP npm installs. Install Node.js + npm, then rerun without --skip-mcp if needed."
    mcp_status="skipped (npm not found)"
  else
    mcp_count=0
    while IFS= read -r package_json; do
      mcp_dir="$(dirname "$package_json")"
      mcp_count=$((mcp_count + 1))
      echo "[INFO] Installing MCP deps in $mcp_dir"
      if [[ -f "$mcp_dir/package-lock.json" ]]; then
        (cd "$mcp_dir" && npm ci)
      else
        (cd "$mcp_dir" && npm install)
      fi
    done < <(find "$REPO_ROOT/skills" -type f -path '*/scripts/mcp/package.json' | sort)

    mcp_status="installed in ${mcp_count} MCP package(s)"
  fi
fi

swmm_status="missing"
if command -v swmm5 >/dev/null 2>&1; then
  swmm_bin="$(command -v swmm5)"
  swmm_ver="$(swmm5 --version 2>/dev/null | head -n 1 || true)"
  if [[ -n "$swmm_ver" ]]; then
    swmm_status="found at $swmm_bin ($swmm_ver)"
  else
    swmm_status="found at $swmm_bin"
  fi
else
  cat <<'SWMM_WARN'
[WARN] SWMM engine not detected: 'swmm5' is not on PATH.
Install EPA SWMM and ensure the CLI binary is available as 'swmm5'.
Common setup paths:
  - macOS (Homebrew): brew install --cask epaswmm
  - Windows: install EPA SWMM and add swmm5.exe to PATH
  - Linux: install/build SWMM and expose 'swmm5' on PATH
Then verify:
  swmm5 --version
SWMM_WARN
fi

cat <<SUMMARY

Install summary
- Repo root: $REPO_ROOT
- Python setup: $python_status
- MCP npm setup: $mcp_status
- SWMM check: $swmm_status

Next steps
1. Activate the virtualenv: source .venv/bin/activate
2. Run acceptance: python3 scripts/acceptance/run_acceptance.py --run-id latest
3. Open report: runs/acceptance/latest/acceptance_report.md
4. Make a plot from acceptance outputs:
   python3 skills/swmm-plot/scripts/plot_rain_runoff_si.py \\
     --inp runs/acceptance/latest/04_builder/model.inp \\
     --out runs/acceptance/latest/05_runner/acceptance.out \\
     --out-png runs/acceptance/latest/07_plot/fig_rain_runoff.png
SUMMARY
