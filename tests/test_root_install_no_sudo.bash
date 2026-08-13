#!/usr/bin/env bash
# tests/test_root_install_no_sudo.bash
#
# Found by running the documented Linux one-liner in a container for the first
# time. Two hardcoded `sudo` calls, two different failures:
#
#   main: line 32: sudo: command not found          <- bootstrap, install died
#   scripts/install.sh: line 268: sudo: command not found
#   apt-get install failed.
#   [ERROR] SWMM engine build failed (non-fatal).
#   ...
#   Install complete.
#   - SWMM engine:  not installed
#
# The second is the worse one: the engine step is non-fatal by design, so the
# install reported success on a machine that could not run a single model.
#
# Containers and many server images run as root and ship no sudo at all. Root
# is the common case for an unattended install, not the exception.
set -euo pipefail
IFS=$'\n\t'

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/.." && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Raw `sudo <command>` calls, ignoring comments, the helpers' own `sudo "$@"`,
# the `command -v sudo` probe, and message text.
raw_sudo_calls() {
  grep -nE '(^|[;&|]|\bthen\b|\bdo\b|&&|\|\|)[[:space:]]*sudo[[:space:]]+[a-z]' "$1" \
    | grep -v '^[0-9]*:[[:space:]]*#' \
    | grep -v 'command -v sudo' \
    | grep -v "sudo \"\\\$@\"" \
    | grep -v 'printf' || true
}

for f in "$REPO_ROOT/scripts/install.sh" "$REPO_ROOT/scripts/bootstrap.sh"; do
  found="$(raw_sudo_calls "$f")"
  if [[ -n "$found" ]]; then
    echo "FAIL: $(basename "$f") calls sudo directly; on a root image with no sudo that is a bare"
    echo "      'sudo: command not found' under set -e. Route it through the root helper."
    echo "$found" | sed 's/^/      /'
    exit 1
  fi
done

# Both files must carry a helper that runs as root when already root.
grep -q 'id -u' "$REPO_ROOT/scripts/install.sh" \
  || fail "install.sh never checks whether it is already root"
grep -q 'id -u' "$REPO_ROOT/scripts/bootstrap.sh" \
  || fail "bootstrap.sh never checks whether it is already root"

# And say something useful when neither root nor sudo is available.
grep -q 'needs root' "$REPO_ROOT/scripts/install.sh" \
  || fail "install.sh must explain when a step needs root and sudo is absent"
grep -qi 'needs root' "$REPO_ROOT/scripts/bootstrap.sh" \
  || fail "bootstrap.sh must explain when git cannot be installed"

# CI must actually run this path; static checks cannot prove an install works.
WF="$REPO_ROOT/.github/workflows/linux-smoke.yml"
[[ -f "$WF" ]] || fail "no Linux install smoke workflow; the bash one-liner is unverified in CI"
grep -q 'image: ubuntu' "$WF" \
  || fail "the Linux smoke must run in a container, where root-without-sudo is the default"
grep -q 'swmm5' "$WF" \
  || fail "the Linux smoke must assert the engine exists; the engine step is non-fatal and used to fail silently"

echo "PASS: root install without sudo locks"
