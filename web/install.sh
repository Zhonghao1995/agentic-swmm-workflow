#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Website entrypoint for:
#   curl -fsSL https://aiswmm.com/install.sh | bash
#
# Optional environment variables:
#   AISWMM_INSTALL_REF=v0.7.2   # pin a tag, or 'main' to track development

REPO="Zhonghao1995/agentic-swmm-workflow"

# Default to the latest published release for a reproducible install; override
# with AISWMM_INSTALL_REF to pin a tag or track 'main'.
REF="${AISWMM_INSTALL_REF:-}"
if [[ -z "$REF" ]]; then
  REF="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
    | grep -m1 '"tag_name"' \
    | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  [[ -n "$REF" ]] || REF="main"
fi
export AISWMM_INSTALL_REF="$REF"

URL="https://raw.githubusercontent.com/${REPO}/${REF}/scripts/bootstrap.sh"

printf '[INFO] Installing Agentic SWMM from %s (%s)\n' "$REPO" "$REF"
printf '[INFO] You will pick your AI provider (OpenAI or Claude) and model after install.\n'

curl -fsSL "$URL" | bash -s -- "$@"
