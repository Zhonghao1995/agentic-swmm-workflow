"""Digest-mode rendering for ``aiswmm interactive`` (PRD-185).

The interactive runtime defaults to a compact, single-line digest
of each tool step plus a final summary block. ``--verbose`` keeps the
old multi-line trace untouched (debugging path is sacred).

This module hosts the pure rendering helpers so the planner / runtime
loop wiring stays a one-liner. Nothing here writes to stdout — callers
own the IO.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Per-tool brief-result extractors
# ---------------------------------------------------------------------------
#
# The digest line ends with ``<brief>`` — a one-line summary of the
# tool's structured return. The PRD requires bespoke extractors for a
# handful of tools the operator sees most often; everything else falls
# back to ``result['summary']`` truncated to one line.


def _brief_list_dir(result: dict[str, Any]) -> str:
    entries = (result.get("results") or {}).get("entries")
    if isinstance(entries, list):
        return f"{len(entries)} entries"
    return ""


def _brief_select_skill(result: dict[str, Any]) -> str:
    name = result.get("skill_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def _brief_run_swmm_inp(result: dict[str, Any]) -> str:
    results = result.get("results") or {}
    run_dir = results.get("runDir") or results.get("run_dir")
    if isinstance(run_dir, str) and run_dir.strip():
        leaf = Path(run_dir).name
        if leaf:
            return leaf
    return ""


def _brief_audit_run(result: dict[str, Any]) -> str:
    results = result.get("results") or {}
    status = results.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    return ""


def _brief_inspect_plot_options(result: dict[str, Any]) -> str:
    # ``inspect_plot_options`` already shapes its summary as
    # "rain=2 nodes=4 attrs=6", which is exactly the brief we want.
    summary = result.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip().splitlines()[0]
    return ""


def _brief_recall_session_history(result: dict[str, Any]) -> str:
    results = result.get("results") or {}
    sessions = results.get("sessions")
    if isinstance(sessions, list):
        return f"{len(sessions)} sessions"
    return ""


_BRIEF_EXTRACTORS = {
    "list_dir": _brief_list_dir,
    "select_skill": _brief_select_skill,
    "run_swmm_inp": _brief_run_swmm_inp,
    "audit_run": _brief_audit_run,
    "inspect_plot_options": _brief_inspect_plot_options,
    "recall_session_history": _brief_recall_session_history,
}


def brief_result(tool_name: str, result: dict[str, Any]) -> str:
    """Return a one-line digest of ``result`` for ``tool_name``.

    Per-tool extractors win when they yield a non-empty string;
    otherwise we fall back to the first line of ``result['summary']``
    so any tool added in the future still produces a meaningful
    digest without per-tool code.
    """
    extractor = _BRIEF_EXTRACTORS.get(tool_name)
    if extractor is not None:
        brief = extractor(result)
        if brief:
            return brief
    summary = result.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip().splitlines()[0]
    return ""


# ---------------------------------------------------------------------------
# Single-line step renderer
# ---------------------------------------------------------------------------
#
# The PRD pins the visible shape of every step row. Two markers and a
# few small bits of glue cover the matrix:
#
#   - ✓ / ✗            outcome marker (pass / fail)
#   - "(read-only, auto)" tag on auto-approved tools
#   - "-> [Y/n]: Y|N"     inline Y/N stamp on prompted tools
#   - "(skipped)"         tail when the user denied a prompted tool
#
# On failure with a non-empty ``error_detail`` the renderer appends
# indented continuation lines so the user sees the full stacktrace
# beneath the step row WITHOUT having to re-run with --verbose.

_OK_MARK = "✓"  # ✓
_FAIL_MARK = "✗"  # ✗
_DETAIL_INDENT = " " * 4


def render_step(
    *,
    step: int,
    tool: str,
    is_read_only: bool,
    prompted: bool,
    approved: bool,
    ok: bool,
    brief: str,
    error_detail: str | None,
) -> str:
    """Render one step row (and any indented detail lines) for digest mode.

    Pure function; the caller writes the result to a stream. The
    return value MAY contain embedded newlines when ``error_detail``
    is provided — that is the auto-expanded stacktrace block.
    """
    head = f"[{step}] {tool}"
    if prompted:
        # Permission was asked. Either the user said Y (call ran) or N
        # (call was skipped). The visible Y/N stamp matches what the
        # user actually typed at the prompt so the scrollback tells
        # the same story.
        answer = "Y" if approved else "N"
        if not approved:
            return f"{head}  -> [Y/n]: N  (skipped)"
        head = f"{head}  -> [Y/n]: {answer}"
    elif is_read_only:
        head = f"{head} (read-only, auto)"
    # Outcome marker + brief
    marker = _OK_MARK if ok else _FAIL_MARK
    if brief:
        row = f"{head}  {marker} {brief}"
    else:
        row = f"{head}  {marker}"
    if not ok and error_detail:
        # Indent the first detail line under "Detail: <line>"; keep
        # subsequent lines at the same 4-space step indent, preserving
        # any in-source leading whitespace (so stack traces and
        # multi-line error messages keep their internal structure).
        detail_lines = error_detail.splitlines() or [error_detail]
        rendered_detail = [f"{_DETAIL_INDENT}Detail: {detail_lines[0]}"]
        rendered_detail.extend(f"{_DETAIL_INDENT}{line}" for line in detail_lines[1:])
        return "\n".join([row, *rendered_detail])
    return row


# ---------------------------------------------------------------------------
# Final summary block (session-end)
# ---------------------------------------------------------------------------
#
# After the planner finishes its turn the digest renderer prints a
# short block that surfaces the few numbers a stormwater modeller
# wants to see at a glance: peak flow at the outfall, runoff /
# routing continuity, run dir. Source fields come from manifest.json
# (PRD-183 ``Run Results`` section). If a session produced no SWMM
# run, the block is omitted entirely.

_SUMMARY_SEPARATOR = "─" * 25


def _format_peak(payload: dict[str, Any]) -> str | None:
    peak = payload.get("peak_flow_at_outfall")
    if not isinstance(peak, dict):
        return None
    node = peak.get("node")
    value = peak.get("value")
    time = peak.get("time")
    if value is None or node is None or time is None:
        return None
    # Keep the precision present in the manifest; format only the
    # composition string. Trailing zeros on a float coming out of
    # JSON survive the round-trip.
    return f"Peak: {value} CMS @ {time} at {node}"


def _format_continuity(payload: dict[str, Any]) -> str | None:
    cont = payload.get("continuity_error")
    if not isinstance(cont, dict):
        return None
    runoff = cont.get("runoff")
    routing = cont.get("routing")
    if runoff is None and routing is None:
        return None
    parts: list[str] = []
    if runoff is not None:
        parts.append(f"runoff {runoff} %")
    if routing is not None:
        parts.append(f"routing {routing} %")
    return f"Continuity: {', '.join(parts)}"


def _block_for_run(run_dir: Path) -> str:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    lines: list[str] = []
    peak_line = _format_peak(payload)
    if peak_line is not None:
        lines.append(peak_line)
    cont_line = _format_continuity(payload)
    if cont_line is not None:
        lines.append(cont_line)
    lines.append(f"Run dir: {run_dir}")
    return "\n".join(lines)


def render_final_summary(run_dirs: list[Path]) -> str:
    """Return the digest's session-end summary block.

    ``run_dirs`` is the list of SWMM run directories the session
    produced (in order). Each dir is rendered as its own peak /
    continuity / run-dir trio, separated by a single dashed line.
    A run_dir without ``manifest.json`` is skipped silently — the
    PRD says a chat-only session contributes no block. When the
    final list of rendered blocks is empty, the function returns
    the empty string so callers can ``if block: print(block)``
    without further branching.
    """
    rendered: list[str] = []
    for run_dir in run_dirs:
        block = _block_for_run(run_dir)
        if block:
            rendered.append(block)
    if not rendered:
        return ""
    return _SUMMARY_SEPARATOR + "\n" + ("\n" + _SUMMARY_SEPARATOR + "\n").join(rendered)


__all__ = ["brief_result", "render_step", "render_final_summary"]
