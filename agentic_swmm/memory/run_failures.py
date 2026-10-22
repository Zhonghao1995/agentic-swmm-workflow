"""Operational run-failure capture (runtime observability).

Why this module exists
----------------------
``negative_lessons`` records *modeling* failures — continuity FAILs,
diverged calibrations, non-physical parameter sets — so the agent avoids
re-proposing a known-bad region. It deliberately does NOT record
*operational* failures: an MCP child process that died mid-call, a tool
handed a path that does not resolve, a SWMM solver error. Yet those are
the failures that dominate real runs, and today they are invisible —
they scroll past in the trace and are never aggregated, so there is no
way to see the real failure distribution or drive fixes from data.

This module captures operational failures in a dedicated
``run_failures.jsonl`` store, kept *separate* from
``negative_lessons.jsonl`` so operational noise never pollutes the
modeling-knowledge recall path.

Backend
-------
JSONL — one line per recorded failure. Same contract as the other
memory stores: atomic append, tolerant of a torn final line on read,
missing file yields ``[]``. A clean run writes nothing (no empty file).

Schema (``SCHEMA_VERSION == "1.1"``)
------------------------------------
- ``run_id``: the run/session directory name — join key with the trace
- ``tool``: the tool whose call failed
- ``failure_class``: enumerated — see ``FAILURE_CLASSES``
- ``summary``: the (truncated) failure summary, verbatim from the tool
- ``recorded_at``: ISO 8601 UTC
- ``pattern`` (1.1): ``tool:failure_class:<normalized summary>``, the key
  a later failure is matched on; paths and long numbers are collapsed
  because they change between runs while the failure does not
- ``fix`` / ``fix_tool`` (1.1, present only when known): what the planner
  did right after this failure that worked, i.e. the same tool with
  changed arguments or another tool instead. Rows without a fix are
  failures nobody recovered from inside the turn.

The failure loop (memory simplification PR 3a, 2026-09-26): the fix is
derived at session end from the ordered tool results, a later failure
with the same pattern gets the fix as a planner hint at failure time
(:func:`failure_hint`), and the session-start digest names known fixes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from agentic_swmm.memory.jsonl_store import append_rows
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = "1.1"

# Operational failure taxonomy. Distinct from negative_lessons' modeling
# ``lesson_type`` enum — these describe how the *runtime* broke, not what
# the *model* got wrong.
FAILURE_CLASSES = frozenset(
    {"mcp_transport", "path_resolution", "swmm_error", "tool_error", "policy_refusal"}
)

# Cap stored summaries so one pathological error string cannot bloat the
# store. The head carries the diagnostic signal; the tail is usually a
# repeated path or stack frame.
_SUMMARY_CAP = 300

# Pattern normalization: a token with a path separator becomes <path>,
# a number of four or more digits (run stamps, dates, sizes) becomes #,
# three-digit SWMM error codes survive.
_PATTERN_CAP = 120
_PATH_TOKEN_RE = re.compile(r"\S*[/\\]\S*")
# A path with spaces becomes several <path> tokens; one is the pattern.
_PATH_RUN_RE = re.compile(r"<path>(?:\s+<path>)+")
_LONG_NUMBER_RE = re.compile(r"\d{4,}")
_WS_RE = re.compile(r"\s+")

# A fix names the arguments that changed; a changed value is described
# by the part that differs (the shared prefix and, when still too long,
# the shared suffix replaced by "..."), then shortened so one long patch
# or path cannot bloat the row. Live finding F-174: the cap used to hit
# first and cut off the useful tail ("06_runner/model.rpt").
_ARG_VALUE_CAP = 80
_FIX_CAP = 300
_BOUNDARY_CHARS = frozenset("/\\ \t\n")

_SWMM_ERROR_RE = re.compile(r"\bERROR\s+\d{3}\b")

_PATH_MARKERS = (
    "could not resolve",
    "must be an existing repository file",
    "must exist inside repository",
    "directory must exist",
    "file not found",
)

# Refusals by design: the allowlist, the product tree, the evidence
# paths. They are recorded and hinted like any failure, but a "fix" for
# one is a detour around a guardrail, so they are never evidence for a
# proposal (skill refinement loop, ADR-0014).
_POLICY_MARKERS = (
    "command is not allowlisted",
    "writes code into the product tree",
    "modifies evidence/generated memory path",
    "blocked by policy",
    "must be inside repository",
    "creates a run folder",
    "edits a repository fixture",
    "writes a new file at the run root",
    "of the audited run",
    "refusing to read",
    "outside the repository's tests/ tree",
    "resolves to non-public address",
)


def _is_permission_denial(result: dict[str, Any]) -> bool:
    """Return True when a result is a user permission denial, not a fault.

    Denials carry ``ok=False`` but they are a deliberate user choice, not
    a runtime fault, so they must never be recorded as run failures.
    """
    perm = result.get("permission")
    if isinstance(perm, dict) and perm.get("approved") is False:
        return True
    # Fallback for stub results that predate the executor's permission seam.
    return str(result.get("summary", "")) == "tool not approved by user"


def classify_failure(result: dict[str, Any]) -> str | None:
    """Classify one tool-result dict into a failure class, or ``None``.

    Returns ``None`` when the result is not a recordable operational
    failure — i.e. it succeeded, or it is a user permission denial.
    """
    if result.get("ok", True):
        return None
    if _is_permission_denial(result):
        return None

    # The stored summary is cut at _SUMMARY_CAP, and the class and the
    # pattern are derived from the stored text on every read; classifying
    # the same capped text here keeps a row's class identical at write
    # time, at hint time and at read time (review of #569).
    summary = str(result.get("summary", ""))[:_SUMMARY_CAP]
    low = summary.lower()

    if any(marker in low for marker in _POLICY_MARKERS):
        return "policy_refusal"

    # Before the transport markers: a SWMM error that arrives through the
    # runner's MCP server carries both (live finding F-176).
    if _SWMM_ERROR_RE.search(summary):
        return "swmm_error"

    if "mcp" in low and (
        "transport" in low
        or "process ended" in low
        or "tools/list" in low
        or "tools/call" in low
        or "unknown mcp server" in low
    ):
        return "mcp_transport"

    if any(marker in low for marker in _PATH_MARKERS):
        return "path_resolution"
    if "not found" in low and any(
        ext in low for ext in (".inp", ".out", ".rpt")
    ):
        return "path_resolution"

    return "tool_error"


def failure_pattern(tool: str, failure_class: str, summary: str) -> str:
    """The key a failure is matched on across runs."""
    text = _PATH_TOKEN_RE.sub("<path>", str(summary))
    text = _PATH_RUN_RE.sub("<path>", text)
    text = _LONG_NUMBER_RE.sub("#", text)
    text = _WS_RE.sub(" ", text).strip().lower()[:_PATTERN_CAP]
    return f"{tool}:{failure_class}:{text}"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)


def _short(value: Any) -> str:
    text = _text(value)
    if len(text) > _ARG_VALUE_CAP:
        return text[: _ARG_VALUE_CAP - 3] + "..."
    return text


def _differing(old: str, new: str) -> tuple[str, str]:
    """The parts of ``old`` and ``new`` that differ, cut at path or word boundaries.

    The shared prefix becomes "..." (a long run directory must not hide
    the segment that changed). The shared suffix is kept while the rest
    fits the value cap, and becomes "..." too when it does not.
    """
    n = len(os.path.commonprefix([old, new]))
    cut = n
    while cut > 0 and old[cut - 1] not in _BOUNDARY_CHARS:
        cut -= 1
    if cut > 0:
        cut -= 1  # keep the boundary character with the differing part
    old_rest, new_rest = old[cut:], new[cut:]
    m = len(os.path.commonprefix([old_rest[::-1], new_rest[::-1]]))
    if m and (len(old_rest) > _ARG_VALUE_CAP or len(new_rest) > _ARG_VALUE_CAP):
        suffix = old_rest[len(old_rest) - m :]
        boundary = next((i for i, ch in enumerate(suffix) if ch in _BOUNDARY_CHARS), None)
        if boundary is not None:
            keep = m - boundary
            old_rest = old_rest[: len(old_rest) - keep] + "..."
            new_rest = new_rest[: len(new_rest) - keep] + "..."
    if cut > 0:
        old_rest, new_rest = "..." + old_rest, "..." + new_rest
    return old_rest, new_rest


def describe_fix(failed: dict[str, Any], fixed: dict[str, Any]) -> tuple[str, str]:
    """Return ``(fix_tool, fix)`` for the successful call after a failure.

    Same tool: the arguments that changed (``key: old -> new``, added,
    dropped), or "the same arguments" when a plain retry worked (a
    transient fault). Another tool: that call, "instead".
    """
    tool = str(fixed.get("tool", ""))
    before = failed.get("args") if isinstance(failed.get("args"), dict) else {}
    after = fixed.get("args") if isinstance(fixed.get("args"), dict) else {}
    if tool == str(failed.get("tool", "")):
        parts: list[str] = []
        for key in sorted(set(before) | set(after)):
            if key not in after:
                parts.append(f"{key} dropped")
            elif key not in before:
                parts.append(f"{key}={_short(after[key])} added")
            elif before[key] != after[key]:
                old, new = _differing(_text(before[key]), _text(after[key]))
                parts.append(f"{key}: {_short(old)} -> {_short(new)}")
        text = f"{tool} again with " + ("; ".join(parts) if parts else "the same arguments")
    else:
        args = ", ".join(f"{key}={_short(value)}" for key, value in sorted(after.items()))
        text = f"{tool}({args}) instead"
    return tool, text[:_FIX_CAP]


@dataclass(frozen=True)
class RunFailure:
    """One row of operational run-failure memory."""

    run_id: str
    tool: str
    failure_class: str
    summary: str
    recorded_at: str | None = None
    pattern: str = ""
    fix: str = ""
    fix_tool: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned dict written to disk."""
        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "tool": self.tool,
            "failure_class": self.failure_class,
            "summary": self.summary,
            "recorded_at": self.recorded_at
            or datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "pattern": self.pattern or failure_pattern(self.tool, self.failure_class, self.summary),
        }
        if self.fix:
            row["fix"] = self.fix
            row["fix_tool"] = self.fix_tool
        return row


def resolve_store(memory_dir: Path | None = None) -> Path:
    """Return the ``run_failures.jsonl`` path.

    Mirrors ``audit_hook._resolve_memory_dir``'s env contract
    (``AISWMM_MEMORY_DIR``) so the operational store sits beside the
    modeling-memory stores by default.
    """
    if memory_dir is not None:
        return Path(memory_dir) / "run_failures.jsonl"
    # Anchored on the repository, not the process cwd: `aiswmm` run from
    # another directory used to create a stray memory/modeling-memory/
    # there and record failures nobody would ever read (finding F-15,
    # 2026-09-02). utils.paths.resolve_memory_dir owns the env override.
    from agentic_swmm.utils.paths import resolve_memory_dir

    return resolve_memory_dir() / "run_failures.jsonl"


def _with_call_args(
    results: Iterable[dict[str, Any]], calls: Sequence[Any] | None
) -> list[dict[str, Any]]:
    """The result dicts, with each call's own arguments when ``calls`` aligns.

    A handler may rewrite the ``args`` it reports on success (apply_patch
    reports ``path_count`` instead of the patch), so a fix described from
    result args read "patch dropped; path_count=1 added" (live finding
    F-170, 2026-09-26). The planner's plan carries the arguments as
    called, one entry per executed result, in order.
    """
    rows = [result for result in results if isinstance(result, dict)]
    if calls is None or len(calls) != len(rows):
        return rows
    aligned: list[dict[str, Any]] = []
    for result, call in zip(rows, calls):
        args = getattr(call, "args", None)
        if isinstance(args, dict) and str(getattr(call, "name", "")) == str(result.get("tool", "")):
            result = {**result, "args": dict(args)}
        aligned.append(result)
    return aligned


def _fix_for(rows: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    """The call that fixed the failure at ``index``, or ``None``.

    The first later success of the same tool in the turn wins: that is
    the retry that worked, even when the planner looked around first
    (live finding F-171, 2026-09-26: the refused ``wc -l`` was fixed by
    ``run_allowed_command`` running pytest twelve steps later, not by
    the ``list_dir`` that came next). Otherwise the call right after
    the failure, when it worked: the planner's reaction, with another
    tool.
    """
    tool = str(rows[index].get("tool", ""))
    for later in rows[index + 1 :]:
        if later.get("ok") and not later.get("skipped") and str(later.get("tool", "")) == tool:
            return later
    following = rows[index + 1] if index + 1 < len(rows) else None
    if following is not None and following.get("ok") and not following.get("skipped"):
        return following
    return None


def record_run_failures(
    store: Path,
    run_id: str,
    results: Iterable[dict[str, Any]],
    calls: Sequence[Any] | None = None,
) -> list[RunFailure]:
    """Append every operational failure in ``results`` to ``store``.

    Scans ``results`` (the executor's per-tool result dicts, in call
    order), classifies each genuine failure (skipping successes and
    permission denials), and appends one JSONL row per failure with the
    call that fixed it when one did (:func:`_fix_for`,
    :func:`describe_fix`). ``calls`` (the planner's plan, one ToolCall
    per result) supplies the arguments as called. Returns the recorded
    failures.

    No-op (returns ``[]``, writes nothing) when there are no failures, so
    a clean run never creates the file.
    """
    rows = _with_call_args(results, calls)
    failures: list[RunFailure] = []
    for index, result in enumerate(rows):
        failure_class = classify_failure(result)
        if failure_class is None:
            continue
        tool = str(result.get("tool", ""))
        summary = str(result.get("summary", ""))[:_SUMMARY_CAP]
        fix_tool = fix = ""
        fixed_by = _fix_for(rows, index)
        if fixed_by is not None:
            fix_tool, fix = describe_fix(result, fixed_by)
        failures.append(
            RunFailure(
                run_id=run_id or "",
                tool=tool,
                failure_class=failure_class,
                summary=summary,
                pattern=failure_pattern(tool, failure_class, summary),
                fix=fix,
                fix_tool=fix_tool,
            )
        )

    if not failures:
        return []

    store = Path(store)
    append_rows(store, (failure.to_dict() for failure in failures))
    return failures


def failure_hint(result: dict[str, Any], store: Path | None = None) -> dict[str, Any] | None:
    """The ``[failure_memory]`` message for a failed call, or ``None``.

    The read side of the failure loop: when this project recorded the
    same failure pattern before and the call after it worked, the planner
    gets that fix in its next turn instead of rediscovering it. Returns
    ``{"content", "pattern", "fix", "fix_tool"}``; ``None`` when the
    result is not a recordable failure or nothing fixed it before.
    """
    failure_class = classify_failure(result)
    if failure_class is None:
        return None
    tool = str(result.get("tool", ""))
    summary = str(result.get("summary", ""))[:_SUMMARY_CAP]
    pattern = failure_pattern(tool, failure_class, summary)
    path = Path(store) if store is not None else resolve_store()
    seen = [row for row in read_run_failures(path) if row.pattern == pattern]
    fixed = [row for row in seen if row.fix]
    if not fixed:
        return None
    last = fixed[-1]
    content = (
        "[failure_memory]\n"
        f"tool: {tool}\n"
        f"failure: {summary}\n"
        f"seen: {len(seen)} time(s) in this project, recovered {len(fixed)} time(s)\n"
        f"last fix: {last.fix}\n"
        "resume: apply that fix if it applies here; otherwise say why it does not."
    )
    return {"content": content, "pattern": pattern, "fix": last.fix, "fix_tool": last.fix_tool}


#: Default window and size of the digest handed to the planner.
DIGEST_DAYS = 7
DIGEST_LIMIT = 8


def recent_failure_digest(
    store: Path | None = None,
    *,
    days: int = DIGEST_DAYS,
    limit: int = DIGEST_LIMIT,
    now: datetime | None = None,
) -> str:
    """Return a ``<recent-failures>`` block for the planner, or ``""``.

    Until 2026-09-02 this store had writers only: every failed tool call
    was recorded and nothing ever read the record back, so a planner
    repeated the same refused ``python -c`` command in session after
    session (live finding F-09). The digest is the read side: the
    distinct (tool, summary) pairs that failed inside ``days``, most
    frequent first, capped at ``limit``, wrapped with one instruction.
    Empty when there is nothing recent, so a clean project pays nothing.
    """
    path = Path(store) if store is not None else resolve_store()
    rows = read_run_failures(path)
    if not rows:
        return ""
    moment = now or datetime.now(timezone.utc)
    cutoff = moment.timestamp() - days * 86400
    counts: dict[tuple[str, str], int] = {}
    latest: dict[tuple[str, str], float] = {}
    fixes: dict[tuple[str, str], str] = {}
    for row in rows:
        stamp = _parse_stamp(row.recorded_at)
        if stamp is None or stamp < cutoff:
            continue
        key = (row.tool, row.summary)
        counts[key] = counts.get(key, 0) + 1
        latest[key] = max(latest.get(key, 0.0), stamp)
        if row.fix:
            fixes[key] = row.fix
    if not counts:
        return ""
    ordered = sorted(counts, key=lambda key: (-counts[key], -latest[key]))[:limit]
    lines = [
        "<recent-failures>",
        f"Tool calls that failed in this project during the last {days} days. "
        "Do not repeat them as written; use the fix recorded next to a line, "
        "or the alternative each hint names "
        "(read_rpt_summary for .rpt data, read_file / search_files for files).",
    ]
    for tool, summary in ordered:
        times = counts[(tool, summary)]
        suffix = f" (x{times})" if times > 1 else ""
        line = f"- {tool}: {summary}{suffix}"
        fix = fixes.get((tool, summary))
        if fix:
            line += f" -> fixed last time by: {fix}"
        lines.append(line)
    lines.append("</recent-failures>")
    return "\n".join(lines)


def _parse_stamp(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def read_run_failures(store: Path) -> list[RunFailure]:
    """Return all recorded failures from ``store`` (``[]`` if missing).

    Tolerant of a torn final line from a concurrent append.
    """
    store = Path(store)
    if not store.is_file():
        return []
    out: list[RunFailure] = []
    from agentic_swmm.memory.store import ledger_rows

    for row in ledger_rows(store):
        tool = str(row.get("tool", ""))
        summary = str(row.get("summary", ""))
        # The class and the pattern are derived from the stored summary
        # on every read, so a row written before a classification or a
        # normalisation fix (F-176, F-174) reads back the way the same
        # failure would be recorded today and matches it.
        failure_class = (classify_failure({"ok": False, "summary": summary}) if summary else None) or str(
            row.get("failure_class", "")
        )
        out.append(
            RunFailure(
                run_id=str(row.get("run_id", "")),
                tool=tool,
                failure_class=failure_class,
                summary=summary,
                recorded_at=row.get("recorded_at"),
                pattern=failure_pattern(tool, failure_class, summary),
                fix=str(row.get("fix") or ""),
                fix_tool=str(row.get("fix_tool") or ""),
            )
        )
    return out
