"""SWMM ``.rpt`` summary-section reader — typed LLM-facing surface.

Family: ``swmm-rpt`` (no MCP server; pure in-process parser).

Why this exists
---------------
``read_file`` (see ``runtime_ops._read_file_tool``) returns at most the
first 4000 characters of a file — roughly the first 25 lines of a SWMM
``.rpt``. The summary sections the LLM actually needs to reason about a
run (Link Flow Summary, Outfall Loading Summary, Node Inflow Summary)
live deep in the file: a typical urban-scale rpt is 300+ KB, and the
Link Flow Summary block starts around line 2500. Without a structured
parser the LLM either:

* burns step budget on ``run_allowed_command grep`` workarounds, or
* truncates ``read_file`` excerpts and silently misses the data.

``read_rpt_summary`` lets the planner ask "what are the top conduits
by peak flow?" in one tool call and get back typed rows it can quote
directly in the final answer.

What it parses
--------------
Three SWMM 5.2.4 summary sections, all hardcoded — the rpt format is
stable enough across SWMM versions that introspecting the column
headers would just add ambiguity:

* ``Link Flow Summary`` (8 fields): peak flow per conduit/orifice.
* ``Outfall Loading Summary`` (5 fields): per-outfall volumes.
* ``Node Inflow Summary`` (7 fields): per-node max inflow and balance
  error.

Each section returns rows sorted by the most decision-relevant column
descending by default (``peak_flow`` for links, ``max_flow`` for
outfalls, ``max_total_inflow`` for nodes), capped at ``top_n`` (1–50).

Parser shape
------------
Section detection: locate the section title line (case-sensitive, the
SWMM writer always writes the exact title). Skip past the column
header block (between rows of dashes). Each subsequent line is split
on whitespace; rows whose token count does not match the expected row
width terminate the section (catches blank lines, ``System`` totals
rows, and the next section's asterisk banner). On a per-row parse
error (e.g. non-numeric token where a float is expected), the row is
skipped — a single malformed line does not fail the whole call.

Validation is fail-soft via :func:`_failure` from ``_shared`` so the
planner sees the same shape as every other handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.swmm_runtime.rpt_summary import (
    SECTIONS as _CANONICAL_SECTIONS,
    SectionSchema as _SectionSchema,
    parse_section as _parse_section,
    parse_variable_section as _parse_variable_section,
)
from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------
#
# The canonical section schemas and ``_parse_section`` implementation live in
# ``agentic_swmm.agent.swmm_runtime.rpt_summary``.  We re-export them under
# the historic private names so the tool-handler code below is unchanged and
# any callers that imported ``_SectionSchema`` or ``_parse_section`` directly
# from this module continue to work.
#
# ``_SECTIONS`` is the lookup dict used by ``_read_rpt_summary_tool`` below.

_SECTIONS: dict[str, _SectionSchema] = _CANONICAL_SECTIONS


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


_MAX_TOP_N = 50
_MIN_TOP_N = 1


def _read_rpt_summary_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Parse one summary section from a SWMM ``.rpt`` file.

    Required:
        ``rpt_path``: repo-relative or absolute path to a ``.rpt`` file
            inside the repository sandbox.
        ``section``: one of ``"Link Flow Summary"``,
            ``"Outfall Loading Summary"``, ``"Node Inflow Summary"``.

    Optional:
        ``top_n``: positive int, clamped to ``[1, 50]``. Defaults to 5.
        ``sort_by``: column name; falls back silently to the section's
            default sort if unknown.
    """

    # Lazy import — ``_required_repo_file`` lives in ``tool_registry``
    # which is the parent of this submodule. Top-level import would
    # create a cycle (tool_registry → tool_handlers/* → tool_registry).
    from agentic_swmm.agent.tool_registry import _required_repo_file

    rpt_path_raw = call.args.get("rpt_path")
    if not isinstance(rpt_path_raw, str) or not rpt_path_raw.strip():
        return _failure(call, "missing required argument: rpt_path")

    section_raw = call.args.get("section")
    if not isinstance(section_raw, str) or not section_raw.strip():
        return _failure(call, "missing required argument: section")

    section_key = section_raw.strip()
    if section_key not in _SECTIONS:
        supported = ", ".join(_SECTIONS)
        return _failure(
            call,
            f"unsupported section: {section_key}. Supported: {supported}",
        )
    schema = _SECTIONS[section_key]

    rpt_path = _required_repo_file(call, "rpt_path", suffix=".rpt")
    if isinstance(rpt_path, dict):
        return rpt_path

    # Clamp top_n. We never fail on a bad value — the LLM gets clamped
    # and a sensible result instead of an error round-trip.
    top_n_raw = call.args.get("top_n", 5)
    if isinstance(top_n_raw, bool) or not isinstance(top_n_raw, int):
        top_n = 5
    else:
        top_n = top_n_raw
    if top_n < _MIN_TOP_N:
        top_n = _MIN_TOP_N
    elif top_n > _MAX_TOP_N:
        top_n = _MAX_TOP_N

    rpt_text = rpt_path.read_text(encoding="utf-8", errors="replace")

    if schema.variable_columns:
        # WQ continuity + load-summary sections: dynamic pollutant columns.
        # top_n / sort_by do not apply — return all rows.
        rows = _parse_variable_section(rpt_text, schema)
        wq_present = bool(rows)
        summary = (
            f"section={section_key} total={len(rows)} "
            f"wq_present={wq_present}"
        )
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "section": section_key,
            "total_rows": len(rows),
            "shown": len(rows),
            "sort_by": None,
            "rows": rows,
            "wq_present": wq_present,
            "summary": summary,
        }

    rows = _parse_section(rpt_text, schema)

    # Resolve sort column. Unknown ``sort_by`` falls back silently.
    sort_by_raw = call.args.get("sort_by")
    if isinstance(sort_by_raw, str) and sort_by_raw.strip() in schema.numeric_fields:
        sort_by = sort_by_raw.strip()
    else:
        sort_by = schema.default_sort

    rows.sort(key=lambda row: row.get(sort_by, 0.0), reverse=True)

    total_rows = len(rows)
    shown_rows = rows[:top_n]

    summary = (
        f"section={section_key} total={total_rows} "
        f"shown={len(shown_rows)} sort={sort_by} desc"
    )
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "section": section_key,
        "total_rows": total_rows,
        "shown": len(shown_rows),
        "sort_by": sort_by,
        "rows": shown_rows,
        "summary": summary,
    }


__all__ = ["_read_rpt_summary_tool", "_parse_variable_section"]
