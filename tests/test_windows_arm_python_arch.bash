#!/usr/bin/env bash
# tests/test_windows_arm_python_arch.bash
#
# Windows on ARM needs an x64 Python, and that is a hard requirement rather
# than a preference:
#
#   Collecting pyogrio
#     Using cached pyogrio-0.13.0.tar.gz        <- no wheel, source fallback
#     GDAL_VERSION must be provided as an environment variable
#
# shapely has published 130 releases and pyogrio 21, and neither has ever
# shipped a win_arm64 wheel, for any Python from cp310 to cp314. On an ARM64
# interpreter pip therefore falls back to building from source and dies on a
# GDAL nobody has installed. The x64 wheels run under the emulation Windows 11
# provides, which is what every working install on these machines has been
# doing already.
#
# This lock exists because the failure is invisible everywhere it is normally
# tested: GitHub's windows-latest runner is x64, so the one check that installs
# on Windows has always had every wheel available to it.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
PS1="$REPO_ROOT/scripts/install.ps1"

fail() { echo "FAIL: $*" >&2; exit 1; }
strip_comments() { grep -vE '^[[:space:]]*#' || true; }

[[ -f "$PS1" ]] || fail "missing $PS1"
body="$(strip_comments <"$PS1")"

# --- 1. the machine's architecture, not the shell's ------------------------
# An x64 PowerShell on an ARM64 machine reports AMD64; the truth is in
# PROCESSOR_ARCHITEW6432 in exactly that case.
grep -q 'PROCESSOR_ARCHITEW6432' <<<"$body" \
  || fail "install.ps1 reads only PROCESSOR_ARCHITECTURE; an emulated shell misreports the machine"

# --- 2. an interpreter's own architecture is checked -----------------------
grep -q 'platform.machine()' <<<"$body" \
  || fail "install.ps1 never asks a candidate Python what architecture it is"

# --- 3. winget is told to fetch the x64 build on ARM -----------------------
grep -qE "'--architecture', 'x64'" <<<"$body" \
  || fail "install.ps1 does not request the x64 Python build on ARM"

# --- 4. an ARM64-only Python is a loud failure, not a silent one -----------
# It is worse than no Python at all: the install appears to work and then dies
# in the dependency step with a GDAL error that names nothing recognisable.
grep -q 'x64 Python is required on Windows on ARM' <<<"$body" \
  || fail "install.ps1 does not fail clearly when only an ARM64 Python is available"
grep -q 'winget install -e --id Python.Python.3.12 --architecture x64' <<<"$body" \
  || fail "the remediation must give the exact command that fixes it"

# --- 5. x64 hosts keep their existing path untouched -----------------------
# The whole change must be a no-op off ARM: the arch arguments are built only
# under the ARM branch.
grep -q 'if ($onArm)' <<<"$body" \
  || fail "the x64 request is not gated on the host being ARM64"

echo "PASS: windows-on-arm python architecture locks"
