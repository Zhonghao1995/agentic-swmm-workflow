#!/usr/bin/env bash
# tests/test_install_ps1_syntax.bash
#
# Minimal smoke test for scripts/install.ps1 — P1-3 in the 2026-05-14
# architecture review (#79). The bash installer has three harness tests;
# the ps1 installer had zero. This script does NOT attempt to drive a full
# install (which would require Windows, real Python, and npm); it instead:
#
#   1. Asserts that `scripts/install.ps1` exists and parses as valid
#      PowerShell (catches surface-level syntax breakage).
#   2. Asserts the documented flag set in `install.ps1` matches the active
#      `param(...)` block (catches doc/runtime drift).
#   3. Asserts the dead flags removed for P1-3 (-SkipSwmm / -SkipSetup /
#      -SwmmVersion) do not reappear.
#   4. Skips cleanly with a "no pwsh" message when PowerShell is absent
#      (so the test is portable for macOS/Linux CI).
#
# The bash flag-removal counterparts are validated indirectly by
# test_install_script_prompts.bash continuing to pass.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
PS1="$REPO_ROOT/scripts/install.ps1"

if [[ ! -f "$PS1" ]]; then
  echo "FAIL: $PS1 not found"
  exit 1
fi

# --- 1. Dead-flag regression lock ----------------------------------------
# These three flags were removed for #79 P1-3 because nothing in the script
# body referenced them. Re-introducing them needs a deliberate test update.
for dead in 'SkipSwmm' 'SkipSetup' 'SwmmVersion'; do
  if grep -q "\\\$$dead\\b" "$PS1"; then
    echo "FAIL: dead flag '\$$dead' reappeared in $PS1 (P1-3)" >&2
    exit 1
  fi
  if grep -q -- "-$dead\\b" "$PS1"; then
    echo "FAIL: dead flag '-$dead' reappeared in $PS1 (P1-3)" >&2
    exit 1
  fi
done

# --- 2. Documented vs active flag parity ---------------------------------
# Pull the documented flag names from the `# Flags:` comment block only
# (avoid matching unrelated occurrences elsewhere), then the active
# `param(...)` block, and check they're the same set.
doc_flags=$(awk '/^# Flags:/,/^#$/' "$PS1" \
  | sed -nE 's/^#[[:space:]]+-([A-Za-z]+).*/\1/p' \
  | sort -u)
param_flags=$(awk '/^param\(/,/^\)/' "$PS1" \
  | grep -Eo '\$[A-Za-z]+' \
  | sed 's/^\$//' \
  | sort -u)
if [[ -z "$doc_flags" || -z "$param_flags" ]]; then
  echo "FAIL: could not extract doc or param flag list from $PS1"
  exit 1
fi
if [[ "$doc_flags" != "$param_flags" ]]; then
  echo "FAIL: install.ps1 doc/param flag drift" >&2
  echo "  doc:   $(echo "$doc_flags" | tr '\n' ' ')" >&2
  echo "  param: $(echo "$param_flags" | tr '\n' ' ')" >&2
  exit 1
fi

# --- 3. ACL-restriction guard --------------------------------------------
# `Do-ApiKey` writes a secret env file. P1-3 added a `SetAccessRuleProtection`
# call so it is no longer world-readable; lock that here so a future cleanup
# does not silently drop the hardening.
if ! grep -q 'SetAccessRuleProtection' "$PS1"; then
  echo "FAIL: install.ps1 no longer restricts ACL on the env file (P1-3)" >&2
  exit 1
fi

# --- 4. Optional pwsh syntax check ---------------------------------------
if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command "
    try {
      [System.Management.Automation.PSParser]::Tokenize(
        (Get-Content -Raw '$PS1'), [ref]\$null) | Out-Null
      exit 0
    } catch {
      Write-Error \$_.Exception.Message
      exit 1
    }
  "
else
  echo "SKIP: pwsh not available — ran static checks only"
fi

echo "OK test_install_ps1_syntax"
