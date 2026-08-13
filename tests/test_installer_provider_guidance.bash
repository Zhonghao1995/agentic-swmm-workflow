#!/usr/bin/env bash
# tests/test_installer_provider_guidance.bash
#
# The install surface used to speak a two-provider vocabulary that the runtime
# outgrew at ADR-0008:
#
#   [INFO] You'll pick your AI provider (OpenAI or Claude) and model after install.
#   ...
#     3. Choose your AI provider and store your key (the only manual step):
#          OpenAI:  aiswmm login
#          Claude:  aiswmm login --anthropic
#
# ROUTES has carried ten routes since then, including keyless ones (the local
# `codex` gateway that fronts a ChatGPT plan, Ollama, LM Studio), and `aiswmm
# setup` is the interactive picker over all of them. None of that was reachable
# from the installer's own output, so a user who wanted the no-API-key path had
# no way to learn it existed.
#
# These locks keep the four install-surface files pointing at the picker. They
# are static and run anywhere.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

SURFACES=(
  "$REPO_ROOT/scripts/install.ps1"
  "$REPO_ROOT/scripts/install.sh"
  "$REPO_ROOT/web/install.ps1"
  "$REPO_ROOT/web/install.sh"
)

fail() { echo "FAIL: $*" >&2; exit 1; }

for f in "${SURFACES[@]}"; do
  [[ -f "$f" ]] || fail "missing $f"
done

# --- 1. no file may present OpenAI/Claude as the whole menu -----------------
for f in "${SURFACES[@]}"; do
  if grep -qiE "OpenAI or Claude|OpenAI or Anthropic" "$f"; then
    fail "$(basename "$f") still advertises 'OpenAI or Claude'; the route table has ten routes"
  fi
done

# --- 2. both stepped installers must name the picker -----------------------
for f in "$REPO_ROOT/scripts/install.ps1" "$REPO_ROOT/scripts/install.sh"; do
  grep -q 'aiswmm setup' "$f" \
    || fail "$(basename "$f") never mentions 'aiswmm setup', so keyless routes stay undiscoverable"
done

# --- 3. the keyless path has to be visible, not just implied ---------------
for f in "$REPO_ROOT/scripts/install.ps1" "$REPO_ROOT/scripts/install.sh"; do
  grep -qi 'no API key' "$f" \
    || fail "$(basename "$f") should say a route exists that needs no API key"
done

# --- 4. the summary must stay a command list, not a menu -------------------
# Reprinting the route list in Next steps made the summary read as a second
# decision to make before anything could be run. The picker lists the routes.
for f in "$REPO_ROOT/scripts/install.ps1" "$REPO_ROOT/scripts/install.sh"; do
  if grep -qE 'OpenRouter, DeepSeek|Ollama, LM Studio' "$f"; then
    fail "$(basename "$f") enumerates routes in the summary; that belongs to aiswmm setup"
  fi
done

# --- 4b. the hand-off to the picker must be guarded ------------------------
# Auto-launching an interactive picker from a piped one-liner is right for a
# human and fatal for CI, which would hang on the first prompt until the job
# times out. Every install surface that launches it must offer the opt-out
# and check for a real console.
for f in "$REPO_ROOT/scripts/install.ps1" "$REPO_ROOT/scripts/install.sh"; do
  if grep -q 'aiswmm setup' "$f" && grep -qE 'aiswmm(\.exe)?" setup|\$venvAiswmm setup' "$f"; then
    grep -q 'AISWMM_NO_SETUP' "$f" \
      || fail "$(basename "$f") launches the picker with no AISWMM_NO_SETUP opt-out"
  fi
done
grep -q 'IsInputRedirected' "$REPO_ROOT/scripts/install.ps1" \
  || fail "install.ps1 must check for a real console before launching the picker"
grep -q -- '-t 0' "$REPO_ROOT/scripts/install.sh" \
  || fail "install.sh must check for a tty before launching the picker"
grep -q 'AISWMM_NO_SETUP' "$REPO_ROOT/.github/workflows/windows-smoke.yml" \
  || fail "windows-smoke.yml must set AISWMM_NO_SETUP; a hung prompt burns the job timeout"

# --- 5. the setup wizard's gateway recipe must cover Windows ---------------
# It used to print only `brew install cliproxyapi`, which is unusable on the
# platform where the codex route is hardest to discover.
WIZARD="$REPO_ROOT/agentic_swmm/commands/setup_wizard.py"
grep -q 'npm install -g omniroute' "$WIZARD" \
  || fail "setup_wizard.py gateway recipe has no non-brew path; Windows users cannot follow it"

# --- 6. doctor must point somewhere beyond the two key rows ----------------
DOCTOR="$REPO_ROOT/agentic_swmm/diagnostics/doctor_report.py"
grep -q "'aiswmm setup'" "$DOCTOR" \
  || fail "doctor's LLM provider section never names 'aiswmm setup'"

echo "PASS: installer provider guidance locks"
