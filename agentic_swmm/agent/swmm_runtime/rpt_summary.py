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

Water-quality sections (``Runoff Quality Continuity``, ``Quality Routing
Continuity``, ``Subcatchment Washoff Summary``, ``Link Pollutant Load
Summary``) are parsed by ``parse_variable_section`` — NOT by ``parse_section``
— because pollutant column names vary by model configuration.  The parity
test does not cover WQ sections; they are exempt.

What is parsed
--------------
Three SWMM 5.2.4 hydrology summary sections via ``parse_section``:

* ``Link Flow Summary`` (8 fields): peak flow per conduit/orifice.
* ``Outfall Loading Summary`` (>= 5 fields): per-outfall volumes + optional
  pollutant load columns when water quality is enabled.
* ``Node Inflow Summary`` (9 raw tokens → 7 dict fields): per-node max
  inflow and balance error.

Four SWMM 5.2.4 water-quality summary sections via ``parse_variable_section``:

* ``Runoff Quality Continuity``: pollutant mass balance at the surface.
* ``Quality Routing Continuity``: pollutant mass balance through routing.
* ``Subcatchment Washoff Summary``: per-subcatchment pollutant loads (kg).
* ``Link Pollutant Load Summary``: per-link pollutant loads (kg).

Each section's rows are returned unsorted in file order.  Callers that need
sorting (e.g. ``swmm_rpt.py``) sort after calling the parse function.

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

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionSchema:
    """Describes how to locate and parse one SWMM summary section.

    ``title``          — the exact section title line as SWMM writes it.
    ``raw_columns``    — expected whitespace-token count per data row.  A row
                         with a fewer tokens terminates section parsing.
                         For the base hydrology sections this is an exact
                         match.  For ``Outfall Loading Summary`` it is a
                         minimum (>= 5) so water-quality pollutant-load
                         columns are accepted without breaking non-WQ runs.
    ``parse``          — callable from token list → row dict; raises on bad
                         input so the row is skipped gracefully.
    ``default_sort``   — the column name for callers that want a default sort.
    ``numeric_fields`` — the field names that carry numeric data; used by
                         callers to validate ``sort_by`` arguments.
    ``variable_columns`` — when True the section has one column per defined
                         pollutant.  ``parse_variable_section`` is used
                         instead of ``parse_section`` for these schemas.
    ``min_columns``    — when True, ``raw_columns`` is a minimum rather than
                         an exact match.  Used for ``Outfall Loading Summary``
                         to handle optional WQ pollutant-load columns.
    """

    title: str
    raw_columns: int
    parse: Callable[[list[str]], dict[str, Any]]
    default_sort: str
    numeric_fields: tuple[str, ...]
    variable_columns: bool = field(default=False)
    min_columns: bool = field(default=False)


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
    # node, freq%, avg_flow, max_flow, total_volume [, pol1, pol2, ...]
    # Extra tokens beyond index 4 are pollutant loads (kg); their column
    # names are resolved at parse time by _parse_outfall_row_wq (for WQ runs).
    # This base parser captures only the 5 core hydraulic fields so
    # non-WQ calls are byte-identical to the pre-WQ behaviour.
    # ``System`` totals row also has >= 5 tokens — filtered in parse_section.
    return {
        "node": tokens[0],
        "flow_freq_pct": float(tokens[1]),
        "avg_flow": float(tokens[2]),
        "max_flow": float(tokens[3]),
        "total_volume_10_6_ltr": float(tokens[4]),
        "pollutant_loads": {},
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
        min_columns=True,
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
    # ------------------------------------------------------------------
    # Water-quality sections (variable_columns=True).
    # These are parsed by parse_variable_section, not parse_section.
    # The ``parse`` stub and ``raw_columns`` fields are unused but kept
    # so the SectionSchema dataclass contract is fulfilled.
    # ------------------------------------------------------------------
    "Runoff Quality Continuity": SectionSchema(
        title="Runoff Quality Continuity",
        raw_columns=0,
        parse=lambda tokens: {},  # unused — parse_variable_section handles this
        default_sort="metric",
        numeric_fields=(),
        variable_columns=True,
    ),
    "Quality Routing Continuity": SectionSchema(
        title="Quality Routing Continuity",
        raw_columns=0,
        parse=lambda tokens: {},  # unused — parse_variable_section handles this
        default_sort="metric",
        numeric_fields=(),
        variable_columns=True,
    ),
    "Subcatchment Washoff Summary": SectionSchema(
        title="Subcatchment Washoff Summary",
        raw_columns=0,
        parse=lambda tokens: {},  # unused — parse_variable_section handles this
        default_sort="name",
        numeric_fields=(),
        variable_columns=True,
    ),
    "Link Pollutant Load Summary": SectionSchema(
        title="Link Pollutant Load Summary",
        raw_columns=0,
        parse=lambda tokens: {},  # unused — parse_variable_section handles this
        default_sort="name",
        numeric_fields=(),
        variable_columns=True,
    ),
}


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def _locate_title(lines: list[str], title: str) -> int:
    """Return the index of the line whose stripped text starts with ``title``, or -1.

    We use ``startswith`` rather than exact equality because some WQ continuity
    section title lines carry a trailing units token on the same line, e.g.::

        Runoff Quality Continuity             kg

    while hydrology sections like ``Node Inflow Summary`` have the title alone.
    ``startswith`` is safe here because all SWMM section titles are unique
    prefixes within any rpt file.
    """
    for idx, line in enumerate(lines):
        if line.strip().startswith(title):
            return idx
    return -1


def _skip_to_second_dash_row(lines: list[str], start: int) -> int:
    """Advance cursor past the column-header block.

    From ``start``, skip to the first ``---`` row (top dashes), then to the
    second ``---`` row (bottom dashes).  Return the cursor one line past the
    bottom dash row — that is the first data row.
    """
    cursor = start
    # Advance to the first dash row.
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past top dash row

    # Advance to the second dash row.
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past bottom dash row
    return cursor


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
    * the token count is below ``schema.raw_columns`` (exact match for
      hydrology sections; minimum-width match for ``Outfall Loading Summary``
      via ``schema.min_columns``),
    * the line starts with another section's asterisk banner.

    ``System`` totals rows (first token == ``"System"``) are skipped.
    Malformed rows (parse() raises) are skipped without aborting.

    For ``Outfall Loading Summary`` with ``min_columns=True``: extra tokens
    beyond the 5 core hydraulic fields are pollutant loads.  The column names
    are resolved from the header line (the line just before the bottom dash
    row) so each pollutant load is stored as ``row["pollutant_loads"][name]``.
    Non-WQ runs have exactly 5 tokens; ``pollutant_loads`` is always present
    but empty for those rows.
    """
    lines = rpt_text.splitlines()
    title_line_idx = _locate_title(lines, schema.title)
    if title_line_idx < 0:
        return []

    # ----------------------------------------------------------------
    # For Outfall Loading Summary (min_columns=True) we need to extract
    # pollutant column names from the header block before advancing to
    # the data rows.  The header block sits between the two dash rows;
    # the last non-dash, non-blank line before the bottom dash row
    # contains the column names.
    # ----------------------------------------------------------------
    pollutant_col_names: list[str] = []
    if schema.min_columns:
        # Find the two dash rows so we can extract the header line.
        hdr_cursor = title_line_idx + 1
        while hdr_cursor < len(lines) and not lines[hdr_cursor].lstrip().startswith("---"):
            hdr_cursor += 1
        top_dash = hdr_cursor
        hdr_cursor += 1
        last_hdr_line = ""
        while hdr_cursor < len(lines) and not lines[hdr_cursor].lstrip().startswith("---"):
            if lines[hdr_cursor].strip():
                last_hdr_line = lines[hdr_cursor]
            hdr_cursor += 1
        # last_hdr_line is the units row e.g. "  Outfall Node  Pcnt  CMS  CMS  10^6 ltr  kg"
        # The pollutant names are on the PRECEDING header line.
        # Actually for Outfall Loading Summary the structure is:
        #   line A: "  Flow  Avg  Max  Total  Total"
        #   line B: "  Freq  Flow  Flow  Volume  TSS" (or no TSS for non-WQ)
        #   line C: "  Outfall Node  Pcnt  CMS  CMS  10^6 ltr  kg" (units)
        # The pollutant names live in the line BEFORE the units row —
        # they are on the same line as "Total" headers.
        # The safest approach: scan all header lines and capture tokens
        # at positions >= 5 (0-indexed) from the last header line, which
        # carries pollutant column names (e.g. "TSS").
        # Check the last_hdr_line: its tokens at position >=4 (after
        # "Pcnt CMS CMS 10^6 ltr") are pollutant units "kg" — not names.
        # The pollutant NAMES are on the second-to-last header line.
        hdr_lines = []
        scan = top_dash + 1
        while scan < len(lines) and not lines[scan].lstrip().startswith("---"):
            if lines[scan].strip():
                hdr_lines.append(lines[scan])
            scan += 1
        # The Outfall Loading Summary header block (3 lines, WQ-enabled)::
        #
        #   line 0: "Flow  Avg   Max   Total  Total"   (hydraulic col headers)
        #   line 1: "Freq  Flow  Flow  Volume  TSS"    (row-2 / pollutant names)
        #   line 2: "Outfall Node  Pcnt  CMS  CMS  10^6 ltr  kg" (units/id line)
        #
        # Non-WQ (2 lines)::
        #
        #   line 0: "Flow  Avg   Max   Total"
        #   line 1: "Outfall Node  Pcnt  CMS  CMS  10^6 ltr"
        #
        # Pollutant names occupy positions >= 4 on hdr_lines[-2].
        # For a non-WQ rpt that has exactly 2 header lines, hdr_lines[-2] is
        # the "Flow Avg Max Total" line — position >= 4 is empty, so
        # pollutant_col_names stays [].
        if len(hdr_lines) >= 2:
            name_tokens = hdr_lines[-2].split()
            # Tokens at index >= 4 are pollutant names (after the 4 fixed
            # hydraulic sub-headers: Freq, Flow, Flow, Volume).
            pollutant_col_names = name_tokens[4:]

    cursor = _skip_to_second_dash_row(lines, title_line_idx + 1)

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped:
            break
        if stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        n = len(tokens)
        if schema.min_columns:
            if n < schema.raw_columns:
                break
        else:
            if n != schema.raw_columns:
                # End-of-section marker — most often blank line / next
                # section / System totals row with a different token count.
                break
        # Outfall ``System`` totals row is a roll-up, not an individual node.
        if tokens[0] == "System":
            cursor += 1
            continue
        try:
            row = schema.parse(tokens)
            # For Outfall Loading Summary: populate pollutant_loads from
            # extra tokens beyond the 5 core hydraulic fields.
            if schema.min_columns and n > 5:
                pol_loads: dict[str, float] = {}
                for i, pol_name in enumerate(pollutant_col_names):
                    tok_idx = 5 + i
                    if tok_idx < n:
                        try:
                            pol_loads[pol_name] = float(tokens[tok_idx])
                        except ValueError:
                            pass
                row["pollutant_loads"] = pol_loads
            rows.append(row)
        except (ValueError, IndexError):
            # Malformed row — skip; one bad line must not fail the section.
            pass
        cursor += 1
    return rows


def parse_variable_section(rpt_text: str, schema: SectionSchema) -> list[dict[str, Any]]:
    """Parse a SWMM summary section with variable pollutant columns.

    Used for the four water-quality sections whose column count depends on
    how many pollutants the model defines:

    * ``Runoff Quality Continuity`` — rows have a ``metric`` key and a
      ``values`` dict mapping pollutant name → float (kg or %).
    * ``Quality Routing Continuity`` — same structure.
    * ``Subcatchment Washoff Summary`` — rows have a ``name`` (subcatchment)
      key and a ``loads`` dict mapping pollutant name → float (kg).
    * ``Link Pollutant Load Summary`` — rows have a ``name`` (link) key and
      a ``loads`` dict mapping pollutant name → float (kg).

    Returns ``[]`` when the section is absent from the rpt.

    The WQ continuity sections have a distinct banner format: pollutant names
    appear on the first ``**...``  line (the asterisk banner), and units on
    the second dash-row line.  The data rows consist of a label followed by
    one float value per pollutant.

    The WQ summary sections (washoff, link load) follow the standard
    header-block format: one pollutant name per column, with a units row
    on the last header line.  Data rows: entity name + one float per pollutant.
    """
    lines = rpt_text.splitlines()
    title_line_idx = _locate_title(lines, schema.title)
    if title_line_idx < 0:
        return []

    title_key = schema.title

    if title_key in ("Runoff Quality Continuity", "Quality Routing Continuity"):
        return _parse_wq_continuity(lines, title_line_idx)
    if title_key in ("Subcatchment Washoff Summary", "Link Pollutant Load Summary"):
        return _parse_wq_entity_loads(lines, title_line_idx)
    return []


def _parse_wq_continuity(lines: list[str], title_line_idx: int) -> list[dict[str, Any]]:
    """Parse a WQ continuity section.

    Banner format (SWMM 5.2.4)::

        **************************           TSS
        Runoff Quality Continuity             kg
        **************************    ----------
        Initial Buildup ..........         0.000
        ...
        Continuity Error (%) .....         0.000

    Pollutant names appear on the opening asterisk banner line (the line
    immediately before the title).  The closing asterisk line is merged with
    the column-separator dashes: ``**...** ----``.  There is no second dash
    row; data starts on the line immediately after this merged banner/dash line.

    Data rows use dot-padding between label and value, so we split on 2+ spaces
    to reliably separate the metric label from the numeric values.
    """
    # Extract pollutant names from the opening asterisk line before the title.
    pol_names: list[str] = []
    for back in range(title_line_idx - 1, max(0, title_line_idx - 5), -1):
        stripped = lines[back].strip()
        if stripped.startswith("*"):
            tokens = stripped.split()
            pol_names = [t for t in tokens if not t.startswith("*") and t != "**"]
            break

    # Advance past the title line to find the closing asterisk/dash line.
    cursor = title_line_idx + 1
    # Skip lines that are part of the banner or unit row (contain asterisks
    # or only non-data content) to land on the first data row.
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped:
            cursor += 1
            continue
        # The combined asterisk+dash closing banner line starts with "*".
        # Once we've passed it, we're in data rows.
        if stripped.startswith("*"):
            cursor += 1
            continue
        # Anything else is the first data row (or a stray blank).
        break

    import re as _re
    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped or stripped.startswith("***") or stripped.startswith("---"):
            break
        # Split on 2+ spaces: label | value1 [| value2 ...]
        parts = _re.split(r"\s{2,}", stripped)
        if len(parts) < 2:
            cursor += 1
            continue
        # Metric label: strip trailing dots and spaces.
        metric = parts[0].rstrip(". ").strip()
        values: dict[str, Any] = {}
        for i, tok in enumerate(parts[1:]):
            tok = tok.strip()
            if not tok:
                continue
            try:
                val = float(tok)
            except ValueError:
                continue
            col_name = pol_names[i] if i < len(pol_names) else f"col{i}"
            values[col_name] = val
        if metric and values:
            rows.append({"metric": metric, "values": values})
        cursor += 1
    return rows


def _parse_wq_entity_loads(lines: list[str], title_line_idx: int) -> list[dict[str, Any]]:
    """Parse Subcatchment Washoff Summary or Link Pollutant Load Summary.

    Header block::

        ----------------------------------
                                     TSS
        Subcatchment                  kg
        ----------------------------------

    Pollutant names are on the second-to-last header line (before the units).
    Data rows: entity_name  val1  [val2 ...].
    ``System`` totals rows are skipped.
    """
    # Find the dash rows bracketing the column header block.
    cursor = title_line_idx + 1
    # Skip blank lines between the banner and the first dash row.
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    top_dash = cursor
    cursor += 1  # past top dash

    # Collect header lines between the two dash rows.
    hdr_lines: list[str] = []
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        if lines[cursor].strip():
            hdr_lines.append(lines[cursor])
        cursor += 1
    cursor += 1  # past bottom dash

    # Pollutant names: second-to-last header line (the one just before units).
    pol_names: list[str] = []
    if len(hdr_lines) >= 2:
        # e.g. "                                 TSS"
        pol_names = hdr_lines[-2].split()
    elif len(hdr_lines) == 1:
        # Only one header line: it carries units; names not available.
        # Fallback: use position index.
        pol_names = []

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        if len(tokens) < 2:
            cursor += 1
            continue
        if tokens[0] == "System":
            cursor += 1
            continue
        name = tokens[0]
        loads: dict[str, float] = {}
        for i, tok in enumerate(tokens[1:]):
            try:
                val = float(tok)
            except ValueError:
                continue
            col_name = pol_names[i] if i < len(pol_names) else f"col{i}"
            loads[col_name] = val
        if loads:
            rows.append({"name": name, "loads": loads})
        cursor += 1
    return rows


__all__ = ["SectionSchema", "SECTIONS", "parse_section", "parse_variable_section"]
