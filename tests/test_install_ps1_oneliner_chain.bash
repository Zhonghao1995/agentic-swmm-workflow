#!/usr/bin/env bash
# tests/test_install_ps1_oneliner_chain.bash
#
# Static (no-pwsh) contract checks for the Windows one-liner chain:
#
#   irm https://aiswmm.com/install.ps1 | iex
#     -> web/install.ps1        (entrypoint: resolve ref/model, fetch bootstrap)
#     -> scripts/bootstrap.ps1  (ensure git, clone/pull, run install.ps1)
#     -> scripts/install.ps1    (the stepped installer)
#
# Before this lock, web/install.ps1 splatted named args (Provider, Model,
# SourceRef, Skip*/Swmm*) into a bootstrap.ps1 whose param() only declared
# -TargetDir, so PowerShell aborted the whole one-liner with
#   "A parameter cannot be found that matches parameter name 'Provider'."
# bootstrap also hard-required Admin and never forwarded the chosen model.
#
# These checks run on macOS/Linux CI without PowerShell. They assert the
# repaired contract; an optional pwsh tokenize runs only when pwsh is present.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
WEB="$REPO_ROOT/web/install.ps1"
BOOT="$REPO_ROOT/scripts/bootstrap.ps1"
INSTALL="$REPO_ROOT/scripts/install.ps1"

for f in "$WEB" "$BOOT" "$INSTALL"; do
  [[ -f "$f" ]] || { echo "FAIL: missing $f" >&2; exit 1; }
done

# --- 1. bootstrap.ps1 must accept the params web/install.ps1 forwards ------
boot_params="$(awk '/^param\(/,/^\)/' "$BOOT")"
for p in Provider Model; do
  if ! grep -q "\$$p\b" <<<"$boot_params"; then
    echo "FAIL: scripts/bootstrap.ps1 param() is missing \$$p (web forwards it -> splat crash)" >&2
    exit 1
  fi
done

# --- 2. bootstrap.ps1 must forward provider+model into install.ps1 --------
# Otherwise the model chosen in web/install.ps1's menu is silently dropped.
if ! grep -q 'install.ps1' "$BOOT"; then
  echo "FAIL: scripts/bootstrap.ps1 no longer invokes install.ps1" >&2
  exit 1
fi
if ! grep -q -- '-Provider $Provider' "$BOOT" || ! grep -q -- '-Model $Model' "$BOOT"; then
  echo "FAIL: scripts/bootstrap.ps1 must run install.ps1 with -Provider \$Provider -Model \$Model" >&2
  exit 1
fi

# --- 3. web/install.ps1 must not splat unsupported keys into bootstrap -----
# The crash came from splatting $args (with Skip*/Swmm*/SourceRef) into a
# bootstrap that only took -TargetDir. Lock the curated $bootstrapArgs path.
if ! grep -q '@bootstrapArgs' "$WEB"; then
  echo "FAIL: web/install.ps1 should splat the curated \$bootstrapArgs hashtable" >&2
  exit 1
fi
if grep -q '@args' "$WEB"; then
  echo "FAIL: web/install.ps1 still splats \$args into bootstrap (the crash path)" >&2
  exit 1
fi
if grep -Eq '\$args\.(SkipSwmm|SkipMcp|SkipSetup|InstallSystemDeps|SwmmExe|SwmmVersion)' "$WEB"; then
  echo "FAIL: web/install.ps1 still forwards a dead Skip*/Swmm* key into bootstrap" >&2
  exit 1
fi
if grep -q 'SourceRef' "$WEB"; then
  echo "FAIL: web/install.ps1 forwards SourceRef, which bootstrap.ps1 does not accept" >&2
  exit 1
fi

# --- 4. admin escalation must be gated on git being missing ---------------
# A non-admin one-click install must work when git is already present (the
# common case), matching the documented "-InstallSystemDeps only" design.
admin_line="$(grep -nE '^[[:space:]]*Ensure-Admin[[:space:]]*$' "$BOOT" | head -1 | cut -d: -f1 || true)"
gitcheck_line="$(grep -nE 'Get-Command git' "$BOOT" | head -1 | cut -d: -f1 || true)"
if [[ -z "$admin_line" ]]; then
  echo "FAIL: could not find the Ensure-Admin call in bootstrap.ps1" >&2
  exit 1
fi
if [[ -z "$gitcheck_line" || "$admin_line" -lt "$gitcheck_line" ]]; then
  echo "FAIL: Ensure-Admin (line ${admin_line:-?}) must sit inside the 'git missing' guard (first Get-Command git at line ${gitcheck_line:-none})" >&2
  exit 1
fi

# --- 5. optional pwsh tokenize (only when PowerShell is available) ---------
if command -v pwsh >/dev/null 2>&1; then
  for f in "$WEB" "$BOOT"; do
    pwsh -NoProfile -Command "
      try {
        [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw '$f'), [ref]\$null) | Out-Null
        exit 0
      } catch { Write-Error \$_.Exception.Message; exit 1 }
    " || { echo "FAIL: pwsh tokenize failed for $f" >&2; exit 1; }
  done
else
  echo "SKIP: pwsh not available — ran static checks only"
fi

echo "OK test_install_ps1_oneliner_chain"
