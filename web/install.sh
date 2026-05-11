#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Website entrypoint for:
#   curl -fsSL https://aiswmm.com/install.sh | bash
#
# Set AISWMM_INSTALL_REF to a tag such as v0.6.0 for reproducible installs:
#   curl -fsSL https://aiswmm.com/install.sh | AISWMM_INSTALL_REF=v0.6.0 bash

REF="${AISWMM_INSTALL_REF:-main}"
REPO="Zhonghao1995/agentic-swmm-workflow"
URL="https://raw.githubusercontent.com/${REPO}/${REF}/scripts/bootstrap.sh"

printf '[INFO] Installing Agentic SWMM from %s (%s)\n' "$REPO" "$REF"
curl -fsSL "$URL" | bash -s -- "$@"
