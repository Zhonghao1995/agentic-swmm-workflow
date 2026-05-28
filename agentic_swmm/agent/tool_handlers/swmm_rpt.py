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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------
#
# Each entry pins:
#
# * ``raw_columns`` — the count of whitespace-separated tokens in a
#   real data row. Used to detect end-of-section (token-count mismatch
#   terminates parsing). The Node Inflow row has nine tokens because
#   SWMM splits "Time of Max" into ``days`` and ``hh:mm`` fields; we
#   parse all nine and then drop the two time fields when emitting
#   the row dict (the prompt-pinned schema does not surface them).
#
# * ``parse(tokens)`` — turns the raw token list into the public dict.
#   Each parser must raise on bad input so the row gets skipped.
#
# * ``default_sort`` — the column name used when the caller did not
#   pass ``sort_by``. Always descending.


@dataclass(frozen=True)
class _SectionSchema:
    title: str
    raw_columns: int
    parse: Callable[[list[str]], dict[str, Any]]
    default_sort: str
    numeric_fields: tuple[str, ...]


def _parse_link_flow_row(tokens: list[str]) -> dict[str, Any]:
    # link, type, peak_flow, days, hh:mm, max_velocity, max_full_flow, max_full_depth
    return {
        "link": tokens[0],
        "type": tokens[1],
        "peak_flow": float(tokens[2]),
        "time_days": int(tokens[3]),
        "time_hhmm": tokens[4],
        "max_velocity": float(tokens[5]),
        "max_full_flow_ratio": float(tokens[6]),
        "max_full_depth_ratio": float(tokens[7]),
    }


def _parse_outfall_row(tokens: list[str]) -> dict[str, Any]:
    # node, freq%, avg_flow, max_flow, total_volume
    # ``System`` totals row also has 5 tokens — we filter it in the
    # main loop by checking the first token literal.
    return {
        "node": tokens[0],
        "flow_freq_pct": float(tokens[1]),
        "avg_flow": float(tokens[2]),
        "max_flow": float(tokens[3]),
        "total_volume_10_6_ltr": float(tokens[4]),
    }


def _parse_node_inflow_row(tokens: list[str]) -> dict[str, Any]:
    # node, type, max_lat_inflow, max_total_inflow, days, hh:mm,
    # lat_vol, tot_vol, error_pct  -> 9 tokens. ``days`` and ``hh:mm``
    # are dropped from the returned dict (not in the prompt's schema).
    return {
        "node": tokens[0],
        "type": tokens[1],
        "max_lateral_inflow": float(tokens[2]),
        "max_total_inflow": float(tokens[3]),
        # tokens[4] = days, tokens[5] = hh:mm — dropped on purpose.
        "lateral_inflow_volume_10_6_ltr": float(tokens[6]),
        "total_inflow_volume_10_6_ltr": float(tokens[7]),
        "flow_balance_error_pct": float(tokens[8]),
    }


_SECTIONS: dict[str, _SectionSchema] = {
    "Link Flow Summary": _SectionSchema(
        title="Link Flow Summary",
        raw_columns=8,
        parse=_parse_link_flow_row,
        default_sort="peak_flow",
        numeric_fields=(
            "peak_flow",
            "time_days",
            "max_velocity",
            "max_full_flow_ratio",
            "max_full_depth_ratio",
        ),
    ),
    "Outfall Loading Summary": _SectionSchema(
        title="Outfall Loading Summary",
        raw_columns=5,
        parse=_parse_outfall_row,
        default_sort="max_flow",
        numeric_fields=(
            "flow_freq_pct",
            "avg_flow",
            "max_flow",
            "total_volume_10_6_ltr",
        ),
    ),
    "Node Inflow Summary": _SectionSchema(
        title="Node Inflow Summary",
        raw_columns=9,
        parse=_parse_node_inflow_row,
        default_sort="max_total_inflow",
        numeric_fields=(
            "max_lateral_inflow",
            "max_total_inflow",
            "lateral_inflow_volume_10_6_ltr",
            "total_inflow_volume_10_6_ltr",
            "flow_balance_error_pct",
        ),
    ),
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_section(rpt_text: str, schema: _SectionSchema) -> list[dict[str, Any]]:
    """Locate ``schema.title`` in ``rpt_text`` and return its data rows.

    The SWMM writer formats each section as::

        <leading whitespace>***** ... *****
        <leading whitespace><Section Title>
        <leading whitespace>***** ... *****
        <blank>
        <leading whitespace>---- ... ----     (top dashes)
        <wrapped column headers, 1 or more lines>
        <leading whitespace>---- ... ----     (bottom dashes)
        data row 1
        ...
        data row N
        <blank line, or ``---`` then ``System`` totals row, or next banner>

    We anchor on the exact title line, then advance past the
    column-header block by counting dash rows: the **second** dash row
    after the title is the start-of-data delimiter. From there each
    line is tokenised; the section ends when:

    * the line is blank,
    * the line starts with ``---`` (Outfall closing rule),
    * the token count diverges from ``schema.raw_columns``,
    * the line starts with another section's asterisk banner.
    """

    lines = rpt_text.splitlines()
    title_line_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == schema.title:
            title_line_idx = idx
            break
    if title_line_idx < 0:
        return []

    # Skip past the trailing asterisk banner and any blank lines.
    cursor = title_line_idx + 1
    # First dash row = top of column header block.
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past the top dash row
    # Second dash row = bottom of column header block (data starts next).
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past the bottom dash row

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped:
            break
        if stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        if len(tokens) != schema.raw_columns:
            # End-of-section marker — most often blank line / next
            # section / System totals row with a different shape.
            break
        # Outfall ``System`` totals row matches the 5-token shape but
        # is a totals roll-up, not an outfall node. Skip it.
        if tokens[0] == "System":
            cursor += 1
            continue
        try:
            rows.append(schema.parse(tokens))
        except (ValueError, IndexError):
            # Malformed row — skip and keep parsing the rest of the
            # section. One bad line must not fail the whole call.
            pass
        cursor += 1
    return rows


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


__all__ = ["_read_rpt_summary_tool"]
