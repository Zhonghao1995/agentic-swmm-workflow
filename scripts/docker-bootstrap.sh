#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

IMAGE="${AGENTIC_SWMM_IMAGE:-ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.5.0}"
COMMAND="${1:-acceptance}"
RUNS_DIR="${AGENTIC_SWMM_RUNS_DIR:-$PWD/agentic-swmm-runs}"

log() {
  printf '[INFO] %s\n' "$*"
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is required. Install Docker Desktop or Docker Engine, then rerun this command."

mkdir -p "$RUNS_DIR"

docker_user_args=()
if command -v id >/dev/null 2>&1; then
  docker_user_args=(--user "$(id -u):$(id -g)")
fi

if [[ "${AGENTIC_SWMM_SKIP_PULL:-0}" == "1" ]]; then
  log "Skipping docker pull for $IMAGE"
else
  log "Pulling $IMAGE"
  docker pull "$IMAGE"
fi

log "Running Agentic SWMM command: $COMMAND"
docker run --rm \
  "${docker_user_args[@]}" \
  -v "$RUNS_DIR:/app/runs" \
  "$IMAGE" \
  "$COMMAND"

cat <<SUMMARY

Docker quickstart complete.
- Image: $IMAGE
- Command: $COMMAND
- Artifacts: $RUNS_DIR
SUMMARY
