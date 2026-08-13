#!/usr/bin/env bash
# tests/test_install_smoke_matrix.bash
#
# Every platform the installer claims to support must have a CI job that runs
# the documented one-liner on that platform. This is not a formality: on
# 2026-08-13 both Windows on ARM and Linux turned out to be completely
# uninstallable, and neither defect was subtle. They survived because the only
# Windows runner was x64 and the bash installer had never been run by CI at
# all, so nothing ever executed the code that was broken.
#
# The assertions here are about coverage, not correctness. A green matrix that
# cannot reach a platform is worth less than a red job that can.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"
WF_DIR="$REPO_ROOT/.github/workflows"

fail() { echo "FAIL: $*" >&2; exit 1; }

runners="$(grep -h 'runs-on:' "$WF_DIR"/*smoke*.yml 2>/dev/null | sed 's/.*runs-on:[[:space:]]*//' | sort -u)"
[[ -n "$runners" ]] || fail "no install smoke workflows at all"

for required in windows-latest windows-11-arm ubuntu-latest macos-latest; do
  grep -qx "$required" <<<"$runners" \
    || fail "no install smoke job on $required; that platform's installer is unverified"
done

# Each smoke must actually install, not merely lint. The one-liner entrypoint
# is the thing users run, so it is the thing that has to be executed.
for wf in "$WF_DIR"/*smoke*.yml; do
  grep -qE 'install\.(ps1|sh)' "$wf" \
    || fail "$(basename "$wf") never runs an installer entrypoint"
done

# And each must check that the install produced something usable. "Install
# complete" was already proven compatible with a missing solver.
for wf in "$WF_DIR"/linux-smoke.yml "$WF_DIR"/macos-smoke.yml; do
  [[ -f "$wf" ]] || fail "missing $wf"
  grep -q 'swmm5' "$wf" \
    || fail "$(basename "$wf") does not assert the solver exists"
  grep -q 'import numpy, matplotlib, pandas, shapely, pyogrio, geopandas, rasterio' "$wf" \
    || fail "$(basename "$wf") does not assert the geospatial stack imports"
done

echo "PASS: install smoke matrix covers every supported platform"
