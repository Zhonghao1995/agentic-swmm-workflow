"""Canonical SWMM .rpt summary-section parser (single source of truth).

Why this module exists
----------------------
Five independent .rpt parsers existed in the codebase before this module.
This is the canonical in-process implementation.  All in-process consumers
(``swmm_rpt.py``, ``postflight.py``, ``compare.py``) import from here so a
SWMM version column change needs a single fix.

Skill scripts (``skills/swmm-runner/``, ``skills/swmm-uncertainty/``) are
intentionally kept standalone / import-free for portability and MCP
subprocess isolation.  They are not repointed here.  The parity test
(``tests/test_rpt_parser_parity.py``) enforces agreement between this
module and the skill scripts.

What is parsed
--------------
Three SWMM 5.2.4 summary sections, hardcoded — the rpt format is stable
enough across SWMM versions that introspecting column headers would add
ambiguity:

* ``Link Flow Summary`` (8 fields): peak flow per conduit/orifice.
* ``Outfall Loading Summary`` (5 fields): per-outfall volumes.
* ``Node Inflow Summary`` (9 raw tokens → 7 dict fields): per-node max
  inflow and balance error.

Each section's rows are returned unsorted in file order.  Callers that need
sorting (e.g. ``swmm_rpt.py``) sort after calling ``parse_section``.

Section end is detected by:
  * blank line,
  * line starting ``---`` (Outfall closing rule),
  * token-count mismatch vs expected row width,
  * next section's asterisk banner (``***``).

``System`` totals rows (first token == ``"System"``) are skipped so callers
see real-node rows only.

On a per-row parse error (non-numeric token where a float is expected) the
row is skipped — a single malformed line does not fail the whole call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionSchema:
    """Describes how to locate and parse one SWMM summary section.

    ``title``        — the exact section title line as SWMM writes it.
    ``raw_columns``  — expected whitespace-token count per data row.  A row
                       with a different count terminates section parsing.
    ``parse``        — callable from token list → row dict; raises on bad input
                       so the row is skipped gracefully.
    ``default_sort`` — the column name for callers that want a default sort.
    ``numeric_fields``— the field names that carry numeric data; used by
                       callers to validate ``sort_by`` arguments.
    """

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
    # ``System`` totals row also has 5 tokens — filtered in parse_section.
    return {
        "node": tokens[0],
        "flow_freq_pct": float(tokens[1]),
        "avg_flow": float(tokens[2]),
        "max_flow": float(tokens[3]),
        "total_volume_10_6_ltr": float(tokens[4]),
    }


def _parse_node_inflow_row(tokens: list[str]) -> dict[str, Any]:
    # node, type, max_lat_inflow, max_total_inflow, days, hh:mm,
    # lat_vol, tot_vol, error_pct  -> 9 tokens.
    # ``days`` and ``hh:mm`` (tokens[4] and tokens[5]) are dropped from
    # the returned dict — callers that need the time field can extract it
    # from the raw tokens before calling this parser.
    return {
        "node": tokens[0],
        "type": tokens[1],
        "max_lateral_inflow": float(tokens[2]),
        "max_total_inflow": float(tokens[3]),
        # tokens[4] = days, tokens[5] = hh:mm — intentionally not returned.
        "lateral_inflow_volume_10_6_ltr": float(tokens[6]),
        "total_inflow_volume_10_6_ltr": float(tokens[7]),
        "flow_balance_error_pct": float(tokens[8]),
    }


SECTIONS: dict[str, SectionSchema] = {
    "Link Flow Summary": SectionSchema(
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
    "Outfall Loading Summary": SectionSchema(
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
    "Node Inflow Summary": SectionSchema(
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
# Core parser
# ---------------------------------------------------------------------------


def parse_section(rpt_text: str, schema: SectionSchema) -> list[dict[str, Any]]:
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

    We anchor on the exact title line, advance past the column-header block
    by counting dash rows (the **second** dash row after the title is the
    start-of-data delimiter), then tokenise each data row.  Section ends
    when:

    * the line is blank,
    * the line starts with ``---`` (Outfall closing rule),
    * the token count diverges from ``schema.raw_columns``,
    * the line starts with another section's asterisk banner.

    ``System`` totals rows (first token == ``"System"``) are skipped.
    Malformed rows (parse() raises) are skipped without aborting.
    """
    lines = rpt_text.splitlines()
    title_line_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == schema.title:
            title_line_idx = idx
            break
    if title_line_idx < 0:
        return []

    # Advance past the trailing asterisk banner and any blank lines to
    # reach the first dash row (top of column header block).
    cursor = title_line_idx + 1
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
            # End-of-section marker — most often blank line / next section /
            # System totals row with a different token count.
            break
        # Outfall ``System`` totals row matches the 5-token shape but is a
        # totals roll-up, not an individual outfall node.
        if tokens[0] == "System":
            cursor += 1
            continue
        try:
            rows.append(schema.parse(tokens))
        except (ValueError, IndexError):
            # Malformed row — skip; one bad line must not fail the section.
            pass
        cursor += 1
    return rows


__all__ = ["SectionSchema", "SECTIONS", "parse_section"]
