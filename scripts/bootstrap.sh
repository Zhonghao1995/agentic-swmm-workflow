#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

REPO_URL="https://github.com/Zhonghao1995/agentic-swmm-workflow.git"
TARGET_DIR="${AGENTIC_SWMM_DIR:-agentic-swmm-workflow}"

log() {
  printf '[INFO] %s\n' "$*"
}

install_git_if_needed() {
  if command -v git >/dev/null 2>&1; then
    return
  fi

  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      fi
      if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
      elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
      fi
      brew install git
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y git
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y git
      elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y git
      else
        printf '[ERROR] git is required and no supported package manager was found.\n' >&2
        exit 1
      fi
      ;;
    *)
      printf '[ERROR] Unsupported platform for bootstrap.sh\n' >&2
      exit 1
      ;;
  esac
}

install_git_if_needed

if [[ -d "$TARGET_DIR/.git" ]]; then
  log "Updating existing checkout in $TARGET_DIR"
  git -C "$TARGET_DIR" pull --ff-only
else
  log "Cloning repository into $TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
fi

exec bash "$TARGET_DIR/scripts/install.sh" --yes "$@"
