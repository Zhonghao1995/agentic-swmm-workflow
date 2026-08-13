#!/usr/bin/env bash
# tests/test_venv_interpreter_mismatch.bash
#
# From a Windows 11 install where every plot failed:
#
#   The following compiled module files exist, but seem incompatible with
#   with either python 'cpython-312' or the platform 'win32':
#     * _multiarray_umath.cp311-win_amd64.pyd
#   The Python version is: Python 3.12
#   The NumPy version is: "2.4.6"
#
# `python -m venv <existing dir>` does not rebuild and does not clear
# site-packages: it repoints pyvenv.cfg and the scripts at the new interpreter
# and leaves the old packages behind. When the resolved interpreter changes
# between installs (3.11 present first, winget adds 3.12 later, Resolve-Python
# prefers 3.12) the venv ends up cpython-312 with cp311 binaries in it. pip
# reports nothing wrong, because the metadata says everything is installed:
# that install's "Python deps" step passed in 7 seconds having done nothing.
#
# Both installers must notice the interpreter changed and rebuild, and both
# must prove the wheels import before calling the step a success.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
SH="$REPO_ROOT/scripts/install.sh"
PS1="$REPO_ROOT/scripts/install.ps1"

fail() { echo "FAIL: $*" >&2; exit 1; }
strip_comments() { grep -vE '^[[:space:]]*#' || true; }

for f in "$SH" "$PS1"; do
  [[ -f "$f" ]] || fail "missing $f"
done

# --- 1. both installers must read the existing venv's interpreter ----------
strip_comments <"$SH" | grep -q 'pyvenv.cfg' \
  || fail "install.sh never inspects pyvenv.cfg; a reused venv keeps the old interpreter's binaries"
strip_comments <"$PS1" | grep -q 'pyvenv.cfg' \
  || fail "install.ps1 never inspects pyvenv.cfg"

# --- 2. and rebuild on a mismatch, not reuse -------------------------------
strip_comments <"$SH" | grep -q 'rm -rf "$VENV_DIR"' \
  || fail "install.sh does not remove a venv built by a different interpreter"
strip_comments <"$PS1" | grep -qE 'Remove-Item -Recurse -Force \$VenvDir' \
  || fail "install.ps1 does not remove a venv built by a different interpreter"

# --- 3. an install is not done until the wheels actually import ------------
# pip's exit code only proves metadata was written. The ABI mismatch that
# started this stayed silent through a green install and surfaced hours later
# as a failed plot.
strip_comments <"$SH" | grep -q 'import numpy, matplotlib, pandas' \
  || fail "install.sh never proves the installed wheels import"
strip_comments <"$PS1" | grep -q 'import numpy, matplotlib, pandas' \
  || fail "install.ps1 never proves the installed wheels import"

# --- 4. doctor must import, not just locate --------------------------------
# find_spec() answers "is this package on disk", not "does it work", so doctor
# printed "numpy - importable OK" while every import in the product failed.
DOCTOR="$REPO_ROOT/agentic_swmm/commands/doctor.py"
grep -q 'importlib.import_module' "$DOCTOR" \
  || fail "doctor still decides importability without importing"

echo "PASS: venv interpreter mismatch locks"
