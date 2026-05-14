"""``resolve_case_id`` — single source of truth for case identification.

Every feature that wants to persist anything beyond a single run
boundary (gap-fill promote, calibration accept, modeling-memory
clustering) calls :func:`resolve_case_id` to know which case the
current run belongs to. The resolver chains four sources, in
precedence order:

1. Explicit ``--case-id`` flag (the caller passes ``declared``).
2. ``session_state["case_id"]`` — the slug cached at session start.
3. A sibling run in the same workflow directory whose
   ``experiment_provenance.json`` already records a ``case_id``.
4. Interactive prompt (when ``interactive=True`` and stdin is a TTY).

When every source is exhausted in non-interactive mode the resolver
raises :class:`CaseIdResolutionError` with a message that points the
user at ``--case-id <slug>``. The PRD's user-story #6 mandates this
fail-loud behaviour so downstream features never silently write to a
"default" case.

The slug grammar is intentionally narrow: ``^[a-z][a-z0-9-]{1,63}$``.
Total length 2–64 chars, lowercase ASCII + digit + hyphen, must start
with a letter. The grammar excludes path separators, dot-traversal,
underscores, and uppercase — every value that survives validation is
safe to use as a directory name under ``cases/`` without further
sanitisation.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


# Slug regex — exposed as a constant so callers (CLI help text, error
# messages) can show the exact rule. The pattern bounds total length
# at 64 chars (1 lead char + 1..63 tail chars) which is comfortably
# below typical filesystem name limits.
CASE_ID_PATTERN = r"^[a-z][a-z0-9-]{1,63}$"
_CASE_ID_RE = re.compile(CASE_ID_PATTERN)


ResolutionSource = Literal["explicit", "session_state", "prior_run", "interactive"]


class CaseIdValidationError(ValueError):
    """Raised when a candidate slug fails the ``CASE_ID_PATTERN`` check."""


class CaseIdResolutionError(RuntimeError):
    """Raised when no source supplies a usable case_id.

    Distinct from :class:`CaseIdValidationError` so callers can tell
    "user passed a bad slug" apart from "user passed nothing and we
    could not infer one".
    """


@dataclass(frozen=True)
class CaseId:
    """A validated case_id, with provenance about where it came from.

    Recording the ``source`` lets the agent log a one-line "resolved
    case_id ``tod-creek`` from prior_run" trace line, which makes
    multi-turn sessions auditable without the modeller having to
    remember which CLI flag they passed.
    """

    value: str
    source: ResolutionSource


def is_valid_case_id(candidate: Any) -> bool:
    """Boolean variant of :func:`validate_case_id` — never raises."""
    if not isinstance(candidate, str):
        return False
    return bool(_CASE_ID_RE.match(candidate))


def validate_case_id(candidate: Any) -> str:
    """Return ``candidate`` if it matches ``CASE_ID_PATTERN``, else raise.

    Raises :class:`CaseIdValidationError` for any non-string input or
    any string that fails the regex. The error message embeds the bad
    value so logs are debuggable; the message also restates the rule
    so the user does not have to consult the docstring.
    """
    if not isinstance(candidate, str):
        raise CaseIdValidationError(
            f"case_id must be a string, got {type(candidate).__name__!r}"
        )
    if not _CASE_ID_RE.match(candidate):
        raise CaseIdValidationError(
            f"case_id {candidate!r} is not a valid slug. "
            f"Required pattern: {CASE_ID_PATTERN} "
            "(lowercase letters/digits/hyphen, must start with a letter, 2-64 chars)."
        )
    return candidate


def _read_prior_run_case_id(run_dir: Path) -> str | None:
    """Look for a sibling run with a recorded ``case_id``.

    ``run_dir`` is the directory aiswmm intends to write *this* run
    into. Its parent is a workflow bucket (e.g. ``runs/2026-05-14/``).
    Siblings in that bucket are candidates: if any of them has a
    ``09_audit/experiment_provenance.json`` carrying a ``case_id``,
    we adopt that slug. Multiple siblings with conflicting case_ids
    are tolerated by returning the most recently modified one — same
    rule the human would apply when resuming a workflow.
    """
    if run_dir is None:
        return None
    parent = run_dir.parent
    if not parent.is_dir():
        return None
    candidates: list[tuple[float, str]] = []
    for sibling in parent.iterdir():
        if not sibling.is_dir() or sibling == run_dir:
            continue
        prov_path = sibling / "09_audit" / "experiment_provenance.json"
        if not prov_path.is_file():
            continue
        try:
            payload = json.loads(prov_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("case_id")
        if isinstance(candidate, str) and is_valid_case_id(candidate):
            try:
                mtime = prov_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((mtime, candidate))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _prompt_for_case_id(prior_choices: list[str] | None = None) -> str | None:
    """Interactive prompt for a case_id. Returns ``None`` on EOF / blank."""
    if not sys.stdin.isatty():
        return None
    if prior_choices:
        sys.stderr.write(
            "Known cases: " + ", ".join(prior_choices) + "\n"
        )
    sys.stderr.write("case_id> ")
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return None
    line = line.strip()
    return line or None


def resolve_case_id(
    *,
    declared: str | None,
    run_dir: Path | None,
    session_state: dict[str, Any] | None,
    interactive: bool = False,
    prior_choices: list[str] | None = None,
) -> CaseId:
    """Resolve a case_id from the four PRD-defined sources.

    Parameters
    ----------
    declared:
        The value of ``--case-id`` (or ``None`` if the flag was not
        passed). Validated immediately; a bad slug raises
        :class:`CaseIdValidationError` rather than falling through.
    run_dir:
        The run directory aiswmm is about to write into. Used to
        infer a case_id from sibling runs in the same workflow
        bucket (source 3). Pass ``None`` to skip this source.
    session_state:
        The session_state dict (typically ``session_state.json``).
        A ``case_id`` key here is the cached slug from a prior turn
        in the same session. Pass ``None`` to skip.
    interactive:
        Permit a stdin prompt when the other three sources fail.
        Defaults to ``False`` so library callers fail-loud unless
        they explicitly opt in.
    prior_choices:
        Optional list of known case_ids to show in the prompt for
        convenience. Only consulted when ``interactive=True``.

    Returns
    -------
    CaseId
        Validated slug plus the source label.

    Raises
    ------
    CaseIdValidationError
        If any non-``None`` candidate fails the slug grammar. The
        resolver does not silently skip a bad explicit/session value
        — a bad explicit slug is a typo the user wants to know
        about, not a hint to fall back to inference.
    CaseIdResolutionError
        If every source is exhausted (non-interactive mode) or the
        interactive prompt returns empty.
    """
    # Source 1: explicit flag wins outright.
    if declared is not None:
        return CaseId(value=validate_case_id(declared), source="explicit")

    # Source 2: session state cached from a prior turn.
    if session_state is not None:
        cached = session_state.get("case_id")
        if cached is not None:
            return CaseId(value=validate_case_id(cached), source="session_state")

    # Source 3: prior run in the same workflow bucket.
    prior = _read_prior_run_case_id(run_dir) if run_dir is not None else None
    if prior is not None:
        # The prior-run value was already validated by is_valid_case_id
        # inside the helper, but re-running validate keeps the seam
        # tight if the helper's pre-check ever drifts.
        return CaseId(value=validate_case_id(prior), source="prior_run")

    # Source 4: interactive prompt (opt-in).
    if interactive:
        prompted = _prompt_for_case_id(prior_choices)
        if prompted is not None:
            return CaseId(value=validate_case_id(prompted), source="interactive")

    raise CaseIdResolutionError(
        "no case_id could be resolved. Pass --case-id <slug>, "
        "or run inside a workflow that already has a recorded case_id. "
        f"Slug rule: {CASE_ID_PATTERN}."
    )


__all__ = [
    "CASE_ID_PATTERN",
    "CaseId",
    "CaseIdResolutionError",
    "CaseIdValidationError",
    "is_valid_case_id",
    "resolve_case_id",
    "validate_case_id",
]
