#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Website entrypoint for:
#   curl -fsSL https://aiswmm.com/install.sh | bash
#
# Optional environment variables:
#   AISWMM_INSTALL_REF=v0.6.4   # reproducible install from a specific tag
#   AISWMM_MODEL=gpt-5.4        # override the default OpenAI model

REF="${AISWMM_INSTALL_REF:-main}"
REPO="Zhonghao1995/agentic-swmm-workflow"
URL="https://raw.githubusercontent.com/${REPO}/${REF}/scripts/bootstrap.sh"

# Default OpenAI model. Locked to gpt-5.5 — override with AISWMM_MODEL if needed.
export AISWMM_MODEL="${AISWMM_MODEL:-gpt-5.5}"

printf '[INFO] Installing Agentic SWMM from %s (%s)\n' "$REPO" "$REF"
printf '[INFO] OpenAI model: %s\n' "$AISWMM_MODEL"

curl -fsSL "$URL" | bash -s -- "$@"
