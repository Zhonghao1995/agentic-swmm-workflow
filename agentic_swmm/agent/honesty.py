"""Runtime honesty primitives — fail loudly when something silent breaks trust.

This module is the shared substrate for the trust-restoring fixes in the
UX polish layer. The runtime had a small set of paths that returned
realistic-looking success when the underlying step had failed or never
actually ran. Each helper here corresponds to one of those holes:

* :func:`scan_rpt_for_errors` walks a SWMM ``.rpt`` for verbatim
  ``ERROR \\d+:`` lines so the post-run gate can refuse to advance.
* :func:`assert_swmm_run_ok` raises :class:`SwmmRunError` when the gate
  scan finds anything. Opt-out via ``AISWMM_DISABLE_HONESTY_LAYER=1``
  preserves the legacy "always return exit 0" behaviour for callers
  that intentionally consume partial runs.
* :data:`STUB_BANNER` is the prominent warning printed at the top of any
  verb whose underlying solver hookup is intentionally synthetic — the
  user has to see it before reading the numbers.
* :func:`emit_silent_override_warning` / :func:`emit_silent_default_warning`
  surface flag interactions a domain user is most likely to miss.
* :func:`fail_fast_if_path_missing` is the small wrapper that turns a
  missing required path into an actionable ``error:`` on stderr
  instead of a confused failure 12 lines later.

The module is intentionally pure (no imports from runtime or commands).
Every helper takes its IO stream as an argument so tests can capture
output with a ``StringIO`` rather than monkey-patching stderr.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import IO


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Single chokepoint so it's easy to grep / change. Keep aligned with the
# doctor's "Runtime knobs" section and the docs entry that explains it.
HONESTY_DISABLE_ENV = "AISWMM_DISABLE_HONESTY_LAYER"


STUB_BANNER = (
    "WARNING: aiswmm calibrate currently runs a synthetic walker.\n"
    "The real SCE-UA/DREAM-zs solver hookup is pending.\n"
    "Results below are deterministic stub output, not a calibration."
)


# Match the canonical SWMM ``ERROR <digits>:`` form. SWMM 5.x writes
# these into ``model.rpt`` whenever it refuses to advance. We pin the
# digit run + colon so we don't false-positive on the narrative phrase
# "error" elsewhere in the report (continuity narratives, peak-flow
# summaries, etc.).
_RPT_ERROR_RE = re.compile(r"^\s*(ERROR\s+\d+:.*)$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SwmmRunError(RuntimeError):
    """Raised when a post-run rpt scan finds at least one ``ERROR \\d+:`` line.

    ``error_lines`` is the verbatim list of matched lines (whitespace
    stripped only on the right). Callers can write the first line to
    stderr for a 1-line summary or join the whole list when verbose
    output is requested.

    ``rpt_path`` is captured so a downstream handler can surface the
    file path alongside the summary.
    """

    def __init__(self, error_lines: list[str], rpt_path: Path):
        self.error_lines = list(error_lines)
        self.rpt_path = rpt_path
        super().__init__(
            f"SWMM solver reported {len(error_lines)} error(s); see {rpt_path}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_honesty_layer_disabled() -> bool:
    """Return True iff ``AISWMM_DISABLE_HONESTY_LAYER`` is set to a truthy value.

    Truthy ≔ non-empty and not in {"0", "false", "False", "no", "No"}.
    Anything else (including "1", "yes", "true") disables the layer.
    """
    value = os.environ.get(HONESTY_DISABLE_ENV)
    if value is None:
        return False
    return value.strip() not in {"", "0", "false", "False", "no", "No"}


def scan_rpt_for_errors(rpt_path: Path) -> list[str]:
    """Return the verbatim ``ERROR \\d+:`` lines in a SWMM ``.rpt`` file.

    Returns an empty list when:

    * the file does not exist (a missing rpt is the runner's failure
      mode to report, not ours),
    * the file is unreadable for any OS reason (permissions, EIO),
    * the body does not match the canonical pattern.

    The line is stripped on the right but preserved verbatim otherwise
    so the caller can surface the exact text SWMM produced.
    """
    try:
        if not rpt_path.exists():
            return []
        text = rpt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    matches: list[str] = []
    for raw in text.splitlines():
        m = _RPT_ERROR_RE.match(raw)
        if m:
            matches.append(m.group(1).rstrip())
    return matches


def assert_swmm_run_ok(rpt_path: Path) -> None:
    """Raise :class:`SwmmRunError` when ``rpt_path`` contains SWMM error lines.

    Opt-out via ``AISWMM_DISABLE_HONESTY_LAYER=1`` returns ``None``
    even when the rpt contains errors — callers that intentionally
    consume partial runs (replay tooling, archival fixers) can preserve
    the legacy "exit 0" path.

    Returns ``None`` on clean reports.
    """
    if is_honesty_layer_disabled():
        return
    errors = scan_rpt_for_errors(rpt_path)
    if errors:
        raise SwmmRunError(errors, rpt_path)


def emit_silent_override_warning(
    stream: IO[str] | None = None,
    *,
    flag_user_set: str,
    flag_user_value: object,
    reason: str,
) -> None:
    """Write a single-line stderr warning when a verb silently ignores user input.

    Wire format::

        warning: <flag_user_set> <flag_user_value> ignored because <reason>

    Example::

        warning: --depth-mm 25 ignored because --idf is set;
            computed depth from IDF is 72.19 mm

    The single-line shape (no embedded newline) keeps the warning
    grep-friendly in log scrapers. Callers that need a longer
    explanation should follow up with a second line; one warning per
    interaction is the rule.

    ``stream`` defaults to :data:`sys.stderr`.
    """
    if stream is None:
        stream = sys.stderr
    line = (
        f"warning: {flag_user_set} {flag_user_value} ignored because {reason}"
    )
    stream.write(line + "\n")


def emit_silent_default_warning(
    stream: IO[str] | None = None,
    *,
    flag_omitted: str,
    default_chosen: object,
    hint: str,
) -> None:
    """Write a single-line stderr notice when a verb picks a non-obvious default.

    Wire format::

        note: <flag_omitted> not supplied, using <default_chosen>; <hint>

    Example::

        note: --shape not supplied, using uniform;
            pass --shape chicago for IDF-driven hyetograph

    ``stream`` defaults to :data:`sys.stderr`.
    """
    if stream is None:
        stream = sys.stderr
    line = (
        f"note: {flag_omitted} not supplied, using {default_chosen}; {hint}"
    )
    stream.write(line + "\n")


def fail_fast_if_path_missing(
    path: Path,
    flag_label: str,
    *,
    stream: IO[str] | None = None,
) -> None:
    """Exit immediately with code 2 when ``path`` does not exist.

    Wire format on stderr::

        error: <flag_label> does not exist: <path>

    Exit code 2 mirrors argparse's convention for "argument is
    structurally wrong" — distinct from exit 1 (runtime failure). A
    scripted pipeline can tell the two apart.

    Returns ``None`` when ``path`` exists (existence only — does not
    check readability or content). Tests that don't want the process
    to die can intercept :class:`SystemExit`.
    """
    if stream is None:
        stream = sys.stderr
    try:
        exists = path.exists()
    except OSError:
        exists = False
    if not exists:
        stream.write(f"error: {flag_label} does not exist: {path}\n")
        raise SystemExit(2)
