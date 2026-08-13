"""Extract the hydraulic result tables a report needs out of a SWMM .rpt.

The Word deliverable used to read only the audit JSON, the run manifest, and
whatever PNGs existed. A user who asked for a report of a successful run got
provenance and QA gates and no hydraulics at all, and reasonably asked where
the node flows were. They were in ``model.rpt`` the whole time: SWMM had
computed them and nothing downstream looked.

``skills/swmm-report/scripts/generate_report.py`` cannot parse the .rpt
itself: it is a portable skill script restricted to stdlib + python-docx +
PyYAML, and the parsers live here in ``agentic_swmm``. So the extraction runs
on this side and lands as one more JSON artifact, which the script reads the
same way it already reads ``model_diagnostics.json``. One parser, one source
of truth, and the portability rule stays intact.

Row parsing is delegated to :mod:`agentic_swmm.agent.swmm_runtime.rpt_summary`,
which already owns the section schemas. This module adds only what a report
needs and that parser deliberately drops: the flow units, and the time of peak.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentic_swmm.agent.swmm_runtime.rpt_summary import (
    SECTIONS,
    parse_section,
    section_data_lines,
)

# Stage directories a run's .rpt can live in, newest layout first. Mirrors the
# canonical run layout (ADR-0004); the flat fallback is for legacy runs.
_RPT_DIRS = ("06_runner", "07_runner", "05_runner", "")
# A client table with 400 rows is not a table. Report the total alongside.
_DEFAULT_TOP_N = 15
_FLOW_UNITS_RE = re.compile(r"Flow\s+Units\s*\.*\s*([A-Za-z]+)")


def find_rpt(run_dir: Path) -> Path | None:
    """Locate the run's .rpt, preferring the canonical runner stage."""
    for sub in _RPT_DIRS:
        directory = run_dir / sub if sub else run_dir
        if not directory.is_dir():
            continue
        candidates = sorted(directory.glob("*.rpt"))
        if candidates:
            return candidates[0]
    return None


def flow_units(rpt_text: str) -> str | None:
    """Flow units declared in the report's analysis options, if present.

    A peak inflow of ``0.002`` with no unit is not a result anyone can use.
    """
    match = _FLOW_UNITS_RE.search(rpt_text)
    return match.group(1).upper() if match else None


def _node_times(rpt_text: str) -> dict[str, str]:
    """Node -> "d hh:mm" time of maximum total inflow.

    The shared row parser drops the two time tokens on purpose, so read them
    off the raw section lines instead of widening its contract.
    """
    times: dict[str, str] = {}
    for line in section_data_lines(rpt_text, "Node Inflow Summary"):
        tokens = line.split()
        if len(tokens) >= 6:
            times[tokens[0]] = f"{tokens[4]} {tokens[5]}"
    return times


def _top(rows: list[dict[str, Any]], key: str, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row.get(key) or 0.0, reverse=True)
    return ordered[:limit]


def build_hydraulic_summary(rpt_path: Path, *, top_n: int = _DEFAULT_TOP_N) -> dict[str, Any]:
    """Parse the report's hydraulic tables into a JSON-ready dict."""
    text = rpt_path.read_text(encoding="utf-8", errors="replace")
    nodes = parse_section(text, SECTIONS["Node Inflow Summary"])
    outfalls = parse_section(text, SECTIONS["Outfall Loading Summary"])
    links = parse_section(text, SECTIONS["Link Flow Summary"])

    times = _node_times(text)
    for row in nodes:
        row["time_of_max"] = times.get(str(row.get("node")), "")

    return {
        "rpt": rpt_path.name,
        "flow_units": flow_units(text),
        "top_n": top_n,
        "counts": {"nodes": len(nodes), "outfalls": len(outfalls), "links": len(links)},
        "nodes": _top(nodes, "max_total_inflow", top_n),
        "outfalls": _top(outfalls, "max_flow", top_n),
        "links": _top(links, "peak_flow", top_n),
    }


def write_hydraulic_summary(
    run_dir: Path, *, top_n: int = _DEFAULT_TOP_N
) -> Path | None:
    """Write ``09_audit/hydraulic_summary.json`` for ``run_dir``.

    Returns the path written, or ``None`` when the run has no .rpt to read.
    Never raises on a malformed report: a report generator must not be the
    thing that fails a run that already succeeded.
    """
    rpt = find_rpt(run_dir)
    if rpt is None:
        return None
    try:
        payload = build_hydraulic_summary(rpt, top_n=top_n)
    except Exception:
        return None
    out_dir = run_dir / "09_audit"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "hydraulic_summary.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return None
    return out_path


__all__ = [
    "build_hydraulic_summary",
    "find_rpt",
    "flow_units",
    "write_hydraulic_summary",
]
