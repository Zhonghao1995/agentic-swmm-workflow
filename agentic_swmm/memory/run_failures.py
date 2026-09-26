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
from typing import Any, Iterable


SCHEMA_VERSION = "1.1"

# Operational failure taxonomy. Distinct from negative_lessons' modeling
# ``lesson_type`` enum — these describe how the *runtime* broke, not what
# the *model* got wrong.
FAILURE_CLASSES = frozenset(
    {"mcp_transport", "path_resolution", "swmm_error", "tool_error"}
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
_LONG_NUMBER_RE = re.compile(r"\d{4,}")
_WS_RE = re.compile(r"\s+")

# A fix names the arguments that changed; values are shortened so one
# long patch or path cannot bloat the row.
_ARG_VALUE_CAP = 80
_FIX_CAP = 300

_SWMM_ERROR_RE = re.compile(r"\bERROR\s+\d{3}\b")

_PATH_MARKERS = (
    "could not resolve",
    "must be an existing repository file",
    "must exist inside repository",
    "directory must exist",
    "file not found",
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

    summary = str(result.get("summary", ""))
    low = summary.lower()

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

    if _SWMM_ERROR_RE.search(summary):
        return "swmm_error"

    return "tool_error"


def failure_pattern(tool: str, failure_class: str, summary: str) -> str:
    """The key a failure is matched on across runs."""
    text = _PATH_TOKEN_RE.sub("<path>", str(summary))
    text = _LONG_NUMBER_RE.sub("#", text)
    text = _WS_RE.sub(" ", text).strip().lower()[:_PATTERN_CAP]
    return f"{tool}:{failure_class}:{text}"


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(text) > _ARG_VALUE_CAP:
        return text[: _ARG_VALUE_CAP - 3] + "..."
    return text


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
                parts.append(f"{key}: {_short(before[key])} -> {_short(after[key])}")
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


def record_run_failures(
    store: Path,
    run_id: str,
    results: Iterable[dict[str, Any]],
) -> list[RunFailure]:
    """Append every operational failure in ``results`` to ``store``.

    Scans ``results`` (the executor's per-tool result dicts, in call
    order), classifies each genuine failure (skipping successes and
    permission denials), and appends one JSONL row per failure. When the
    call right after a failure succeeded, that call is recorded as the
    failure's fix (:func:`describe_fix`): it is the planner's reaction to
    the failure, and it worked. Returns the recorded failures.

    No-op (returns ``[]``, writes nothing) when there are no failures, so
    a clean run never creates the file.
    """
    rows = [result for result in results if isinstance(result, dict)]
    failures: list[RunFailure] = []
    for index, result in enumerate(rows):
        failure_class = classify_failure(result)
        if failure_class is None:
            continue
        tool = str(result.get("tool", ""))
        summary = str(result.get("summary", ""))[:_SUMMARY_CAP]
        fix_tool = fix = ""
        following = rows[index + 1] if index + 1 < len(rows) else None
        if following is not None and following.get("ok") and not following.get("skipped"):
            fix_tool, fix = describe_fix(result, following)
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
        failure_class = str(row.get("failure_class", ""))
        summary = str(row.get("summary", ""))
        out.append(
            RunFailure(
                run_id=str(row.get("run_id", "")),
                tool=tool,
                failure_class=failure_class,
                summary=summary,
                recorded_at=row.get("recorded_at"),
                # Rows written before 1.1 carry no pattern; derive it so
                # they count as "seen" for the hint.
                pattern=str(row.get("pattern") or failure_pattern(tool, failure_class, summary)),
                fix=str(row.get("fix") or ""),
                fix_tool=str(row.get("fix_tool") or ""),
            )
        )
    return out
