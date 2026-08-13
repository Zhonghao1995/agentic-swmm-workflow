#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

REPO_URL="https://github.com/Zhonghao1995/agentic-swmm-workflow.git"
TARGET_DIR="${AGENTIC_SWMM_DIR:-agentic-swmm-workflow}"
REF="${AISWMM_INSTALL_REF:-main}"

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
      # sudo is not a given. Containers and plenty of server images run as
      # root and ship no sudo at all, where the hardcoded call died as
      # "sudo: command not found" under set -e: a bare shell error, with
      # nothing said about git, package managers, or what to do next.
      _as_root() {
        if [[ "$(id -u)" -eq 0 ]]; then
          "$@"
        elif command -v sudo >/dev/null 2>&1; then
          sudo "$@"
        else
          printf '[ERROR] git is missing, and installing it needs root.\n' >&2
          printf '        This shell is not root and sudo is not available.\n' >&2
          printf '        Install git yourself and re-run, for example:\n' >&2
          printf '          apt-get install -y git   # or dnf/yum/apk\n' >&2
          return 1
        fi
      }
      if command -v apt-get >/dev/null 2>&1; then
        _as_root apt-get update
        _as_root apt-get install -y git
      elif command -v dnf >/dev/null 2>&1; then
        _as_root dnf install -y git
      elif command -v yum >/dev/null 2>&1; then
        _as_root yum install -y git
      elif command -v apk >/dev/null 2>&1; then
        _as_root apk add --no-cache git
      elif command -v pacman >/dev/null 2>&1; then
        _as_root pacman -Sy --noconfirm git
      else
        printf '[ERROR] git is required and no supported package manager was found.\n' >&2
        printf '        Tried: apt-get, dnf, yum, apk, pacman. Install git and re-run.\n' >&2
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
  log "Updating existing checkout in $TARGET_DIR ($REF)"
  git -C "$TARGET_DIR" fetch --depth 1 origin "$REF"
  git -C "$TARGET_DIR" checkout --detach FETCH_HEAD
else
  log "Cloning $REPO_URL ($REF) into $TARGET_DIR"
  git clone --depth 1 --branch "$REF" "$REPO_URL" "$TARGET_DIR"
fi

exec bash "$TARGET_DIR/scripts/install.sh" --yes
