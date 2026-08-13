#!/usr/bin/env bash
# tests/test_install_ps1_step_harness.bash
#
# Regression locks for the Windows installer's step harness, from a Windows 11
# install where every symptom was a harness bug rather than a real dependency
# problem:
#
#   Step 3/6: MCP servers npm install
#     [FAIL] MCP servers npm install (48s)
#   ----- command output -----      <- empty
#   --------------------------
#   Step 4/6: Initialize ~/.aiswmm/ directory   <- ran anyway, after a FAIL
#     [FAIL] Initialize ~/.aiswmm/ directory (0s)   <- a bare New-Item "failed"
#   [ERROR] Skill files copy failed failed.
#
# Four distinct defects produced that transcript:
#
#   1. Run-Step printed the captured log with `Get-Content`, which writes to the
#      OUTPUT stream. PowerShell appends that to the function's return value, so
#      `-not (Run-Step ...)` evaluated a multi-element array (always truthy) and
#      the caller skipped its failure branch: a failed step neither printed its
#      output nor stopped the install.
#   2. Run-Step read the sticky $LASTEXITCODE without clearing it first, so npm's
#      non-zero exit re-failed the next step, whose body runs no native command.
#   3. Do-McpInstall enumerated mcp/**/package.json with -Recurse, descending into
#      node_modules from an earlier install and running npm in every nested
#      dependency directory. The bash installer is depth-limited; this was not.
#   4. Install-PythonViaWinget / Install-NodeViaWinget leaked `& winget` output
#      into their return values, so `-not (Install-NodeViaWinget)` never fired and
#      the "MCP servers will be skipped" fallback was unreachable.
#
# Static checks: they run on macOS/Linux CI with no PowerShell present.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
INSTALL="$REPO_ROOT/scripts/install.ps1"
BOOT="$REPO_ROOT/scripts/bootstrap.ps1"

for f in "$INSTALL" "$BOOT"; do
  [[ -f "$f" ]] || { echo "FAIL: missing $f" >&2; exit 1; }
done

fail() { echo "FAIL: $*" >&2; exit 1; }

# Slice one `function <Name> {` ... `^}` block out of a ps1 file.
ps_function() {
  awk -v want="function $2 {" '
    index($0, want) == 1 { inside = 1 }
    inside { print }
    inside && /^}/ { exit }
  ' "$1"
}

# Drop full-line comments: these locks are about executable code, and the
# comments deliberately name the constructs being banned.
strip_comments() { grep -vE '^[[:space:]]*#' || true; }

# --- 1. Run-Step must not write to the output stream ------------------------
run_step="$(ps_function "$INSTALL" 'Run-Step' | strip_comments)"
[[ -n "$run_step" ]] || fail "Run-Step not found in $INSTALL"

if grep -qE '^\s*Get-Content' <<<"$run_step"; then
  fail "Run-Step uses Get-Content: its output joins the return value, so the caller's -not (Run-Step ...) sees a truthy array and walks past failed steps"
fi
if grep -q 'Tee-Object' <<<"$run_step"; then
  fail "Run-Step uses Tee-Object: a terminating error in the step body tears the pipeline down before the log is flushed, losing the failure output"
fi
grep -q 'foreach ($line in $lines) { Write-Host $line }' <<<"$run_step" \
  || fail "Run-Step must print the captured step output with Write-Host (host stream, not the return value)"

# --- 2. Run-Step must clear the sticky exit code before running the step -----
grep -q '\$global:LASTEXITCODE = 0' <<<"$run_step" \
  || fail "Run-Step must reset \$LASTEXITCODE before invoking the step body, or a previous native failure re-fails the next step"
reset_line="$(grep -n '\$global:LASTEXITCODE = 0' <<<"$run_step" | head -1 | cut -d: -f1)"
invoke_line="$(grep -n '& \$Action' <<<"$run_step" | head -1 | cut -d: -f1)"
[[ "$reset_line" -lt "$invoke_line" ]] \
  || fail "the \$LASTEXITCODE reset must come before '& \$Action' in Run-Step"

# --- 3. MCP enumeration must be depth-limited to mcp/<server>/package.json ---
mcp="$(ps_function "$INSTALL" 'Do-McpInstall' | strip_comments)"
[[ -n "$mcp" ]] || fail "Do-McpInstall not found in $INSTALL"
if grep -q -- '-Recurse' <<<"$mcp"; then
  fail "Do-McpInstall uses -Recurse: it descends into node_modules from a previous install and runs npm in every nested dependency"
fi
grep -q -- '-Directory' <<<"$mcp" \
  || fail "Do-McpInstall should enumerate mcp/<server> directories, mirroring the bash installer's -mindepth 2 -maxdepth 2"

# --- 4. winget output must not leak into the install helpers' return values --
for fn in Install-PythonViaWinget Install-NodeViaWinget; do
  body="$(ps_function "$INSTALL" "$fn" | strip_comments)"
  [[ -n "$body" ]] || fail "$fn not found in $INSTALL"
  grep -q 'winget install' <<<"$body" || fail "$fn no longer calls winget install"
  grep -qE 'winget install .*\| Out-Host|Out-Host' <<<"$body" \
    || fail "$fn must send winget output to the host, not the output stream, or it returns a truthy array whatever winget did"
done

# --- 5. Fail-Step labels must not already end in 'failed' -------------------
# Print-Failure renders "<label> failed." — a label ending in "failed" printed
# "Skill files copy failed failed."
while IFS= read -r line; do
  label="$(sed -E 's/.*Fail-Step "([^"]*)".*/\1/' <<<"$line")"
  case "$label" in
    *failed) fail "Fail-Step label '$label' renders as '<label> failed.' -> double 'failed'" ;;
  esac
done < <(grep -E 'Fail-Step "' "$INSTALL" | grep -v '^function')

# --- 6. bootstrap.ps1 must check every git exit code -----------------------
# $ErrorActionPreference='Stop' does not apply to native commands in Windows
# PowerShell, so an aborted checkout silently reinstalled the previous tree.
grep -q 'function Invoke-Git' "$BOOT" \
  || fail "bootstrap.ps1 must route git through a helper that checks \$LASTEXITCODE"
for verb in fetch checkout clone; do
  if strip_comments <"$BOOT" | grep -qE "^\s*(& )?git .*\b$verb\b"; then
    fail "bootstrap.ps1 calls 'git $verb' directly; use Invoke-Git so a non-zero exit stops the install"
  fi
done
# The forced checkout is the fix for untracked mcp/*/package-lock.json blocking
# every upgrade from a pre-lockfile install; keep it.
grep -q "'checkout', '--detach', '--force', 'FETCH_HEAD'" "$BOOT" \
  || fail "bootstrap.ps1 must force the detached checkout, or untracked npm lockfiles block the upgrade"

# --- 7. Optional: real parse when PowerShell is available -------------------
if command -v pwsh >/dev/null 2>&1; then
  for f in "$INSTALL" "$BOOT"; do
    pwsh -NoProfile -Command "
      \$errors = \$null
      [System.Management.Automation.Language.Parser]::ParseFile('$f', [ref]\$null, [ref]\$errors) | Out-Null
      if (\$errors.Count -gt 0) { \$errors | ForEach-Object { Write-Host \$_ }; exit 1 }
    " || fail "$f does not parse as PowerShell"
  done
else
  echo "note: pwsh not present; parse check skipped"
fi

echo "PASS: install.ps1 step harness locks"
