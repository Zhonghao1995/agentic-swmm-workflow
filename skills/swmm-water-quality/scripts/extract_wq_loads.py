#!/usr/bin/env python3
"""Extract water-quality load summaries from a SWMM .rpt file.

Stdlib-only — no agentic_swmm imports.  This script is called by the audit
pipeline (via importlib) and can also be run standalone:

    python3 extract_wq_loads.py --rpt path/to/model.rpt
    python3 extract_wq_loads.py --rpt path/to/model.rpt --out-json loads.json

Output (stdout JSON):

    {
        "ok": true,
        "wq_present": true,
        "pollutants": ["TSS"],
        "runoff_quality_continuity": [
            {"metric": "Initial Buildup", "values": {"TSS": 0.0}},
            ...
            {"metric": "Continuity Error (%)", "values": {"TSS": 0.0}}
        ],
        "quality_routing_continuity": [
            {"metric": "Dry Weather Inflow", "values": {"TSS": 0.0}},
            ...
            {"metric": "Continuity Error (%)", "values": {"TSS": -38.789}}
        ],
        "subcatchment_washoff": [
            {"name": "S1", "loads": {"TSS": 0.109}},
            ...
        ],
        "link_loads": [
            {"name": "C1", "loads": {"TSS": 0.295}},
            ...
        ],
        "outfall_loads": [
            {"node": "OF1", "flow_freq_pct": 84.43, "avg_flow": 0.026,
             "max_flow": 0.058, "total_volume_10_6_ltr": 0.081,
             "pollutant_loads": {"TSS": 0.524}}
        ]
    }

When WQ is not enabled in the rpt:

    {"ok": true, "wq_present": false}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# WQ detection
# ---------------------------------------------------------------------------


def _wq_enabled(rpt_text: str) -> bool:
    """Return True iff the rpt was produced by a WQ-enabled run."""
    return "Water Quality .......... YES" in rpt_text


# ---------------------------------------------------------------------------
# Title location helper
# ---------------------------------------------------------------------------


def _locate_title(lines: list[str], title: str) -> int:
    """Return line index whose stripped text starts with ``title``, or -1."""
    for idx, line in enumerate(lines):
        if line.strip().startswith(title):
            return idx
    return -1


# ---------------------------------------------------------------------------
# WQ continuity parser
# ---------------------------------------------------------------------------


def _parse_wq_continuity(lines: list[str], title_line_idx: int) -> list[dict[str, Any]]:
    """Parse Runoff/Routing Quality Continuity rows.

    Banner::

        **************************           TSS
        Runoff Quality Continuity             kg
        **************************    ----------
        Initial Buildup ..........         0.000
        ...
    """
    # Pollutant names from the opening asterisk line before the title.
    pol_names: list[str] = []
    for back in range(title_line_idx - 1, max(0, title_line_idx - 5), -1):
        stripped = lines[back].strip()
        if stripped.startswith("*"):
            tokens = stripped.split()
            pol_names = [t for t in tokens if not t.startswith("*") and t != "**"]
            break

    # Advance past the title and the combined asterisk+dash closing banner.
    cursor = title_line_idx + 1
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped:
            cursor += 1
            continue
        if stripped.startswith("*"):
            cursor += 1
            continue
        break  # first data row

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("***") or stripped.startswith("---"):
            break
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) < 2:
            cursor += 1
            continue
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


# ---------------------------------------------------------------------------
# WQ entity load parser (washoff summary / link load summary)
# ---------------------------------------------------------------------------


def _parse_wq_entity_loads(lines: list[str], title_line_idx: int) -> list[dict[str, Any]]:
    """Parse Subcatchment Washoff Summary or Link Pollutant Load Summary."""
    cursor = title_line_idx + 1
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    cursor += 1  # past top dash

    hdr_lines: list[str] = []
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        if lines[cursor].strip():
            hdr_lines.append(lines[cursor])
        cursor += 1
    cursor += 1  # past bottom dash

    pol_names: list[str] = []
    if len(hdr_lines) >= 2:
        pol_names = hdr_lines[-2].split()
    elif len(hdr_lines) == 1:
        pol_names = []

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        if len(tokens) < 2 or tokens[0] == "System":
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


# ---------------------------------------------------------------------------
# Outfall Loading Summary parser (handles >= 5 tokens, optional WQ columns)
# ---------------------------------------------------------------------------


def _parse_outfall_loading(lines: list[str], title_line_idx: int) -> list[dict[str, Any]]:
    """Parse Outfall Loading Summary, including optional WQ pollutant columns."""
    cursor = title_line_idx + 1
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        cursor += 1
    top_dash = cursor
    cursor += 1  # past top dash

    hdr_lines: list[str] = []
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("---"):
        if lines[cursor].strip():
            hdr_lines.append(lines[cursor])
        cursor += 1
    cursor += 1  # past bottom dash

    pol_names: list[str] = []
    if len(hdr_lines) >= 2:
        name_tokens = hdr_lines[-2].split()
        pol_names = name_tokens[4:]  # tokens at index >= 4 are pollutant names

    rows: list[dict[str, Any]] = []
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("***"):
            break
        tokens = stripped.split()
        if len(tokens) < 5 or tokens[0] == "System":
            cursor += 1
            continue
        try:
            row: dict[str, Any] = {
                "node": tokens[0],
                "flow_freq_pct": float(tokens[1]),
                "avg_flow": float(tokens[2]),
                "max_flow": float(tokens[3]),
                "total_volume_10_6_ltr": float(tokens[4]),
                "pollutant_loads": {},
            }
            if len(tokens) > 5:
                pol_loads: dict[str, float] = {}
                for i, pol_name in enumerate(pol_names):
                    tok_idx = 5 + i
                    if tok_idx < len(tokens):
                        try:
                            pol_loads[pol_name] = float(tokens[tok_idx])
                        except ValueError:
                            pass
                row["pollutant_loads"] = pol_loads
            rows.append(row)
        except (ValueError, IndexError):
            pass
        cursor += 1
    return rows


# ---------------------------------------------------------------------------
# Pollutant name extraction from WQ continuity results
# ---------------------------------------------------------------------------


def _extract_pollutants(
    runoff_cont: list[dict[str, Any]],
    routing_cont: list[dict[str, Any]],
    washoff: list[dict[str, Any]],
    link_loads: list[dict[str, Any]],
    outfall_loads: list[dict[str, Any]],
) -> list[str]:
    """Return sorted list of pollutant names found in any WQ section."""
    names: set[str] = set()
    for row in runoff_cont + routing_cont:
        names.update(row.get("values", {}).keys())
    for row in washoff + link_loads:
        names.update(row.get("loads", {}).keys())
    for row in outfall_loads:
        names.update((row.get("pollutant_loads") or {}).keys())
    return sorted(names)


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_wq_loads(rpt_text: str) -> dict[str, Any]:
    """Parse all WQ sections from ``rpt_text`` and return a structured dict.

    Returns ``{"ok": True, "wq_present": False}`` when WQ is not enabled.
    """
    if not _wq_enabled(rpt_text):
        return {"ok": True, "wq_present": False}

    lines = rpt_text.splitlines()

    runoff_idx = _locate_title(lines, "Runoff Quality Continuity")
    routing_idx = _locate_title(lines, "Quality Routing Continuity")
    washoff_idx = _locate_title(lines, "Subcatchment Washoff Summary")
    link_idx = _locate_title(lines, "Link Pollutant Load Summary")
    outfall_idx = _locate_title(lines, "Outfall Loading Summary")

    runoff_cont = _parse_wq_continuity(lines, runoff_idx) if runoff_idx >= 0 else []
    routing_cont = _parse_wq_continuity(lines, routing_idx) if routing_idx >= 0 else []
    washoff = _parse_wq_entity_loads(lines, washoff_idx) if washoff_idx >= 0 else []
    link_loads = _parse_wq_entity_loads(lines, link_idx) if link_idx >= 0 else []
    outfall_loads = _parse_outfall_loading(lines, outfall_idx) if outfall_idx >= 0 else []

    pollutants = _extract_pollutants(runoff_cont, routing_cont, washoff, link_loads, outfall_loads)

    return {
        "ok": True,
        "wq_present": True,
        "pollutants": pollutants,
        "runoff_quality_continuity": runoff_cont,
        "quality_routing_continuity": routing_cont,
        "subcatchment_washoff": washoff,
        "link_loads": link_loads,
        "outfall_loads": outfall_loads,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract water-quality load summaries from a SWMM .rpt file."
    )
    ap.add_argument("--rpt", required=True, type=Path, help="Path to SWMM .rpt file.")
    ap.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional path to write JSON output (also printed to stdout).",
    )
    args = ap.parse_args(argv)

    rpt_path = args.rpt
    if not rpt_path.exists():
        print(
            json.dumps({"ok": False, "error": f"rpt not found: {rpt_path}"}),
            file=sys.stderr,
        )
        return 1

    rpt_text = rpt_path.read_text(encoding="utf-8", errors="replace")
    result = extract_wq_loads(rpt_text)
    output = json.dumps(result, indent=2)
    print(output)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
