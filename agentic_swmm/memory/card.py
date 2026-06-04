"""Plain-text per-case memory card for ``aiswmm memory show <case>``.

Reads the per-case modeling-memory stores (parametric_memory,
calibration_memory, negative_lessons) and renders one human-readable card so a
modeller can see, at a glance, what aiswmm remembers about a watershed: typical
QA results, the run history, accepted calibrations, and known-bad parameter
regions.

Read-only. Plain ASCII (no emoji). Degrades gracefully when a store is empty
or a case has no memory yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_case_rows(path: Path, case: str) -> list[dict[str, Any]]:
    """Return JSONL rows in ``path`` whose ``case_name`` matches ``case``."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("case_name") == case:
            rows.append(rec)
    return rows


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt_range(values: list[float], unit: str = "") -> str:
    suffix = f" {unit}" if unit else ""
    lo, hi = min(values), max(values)
    if lo == hi:
        return f"{lo:g}{suffix}"
    return f"{lo:g} .. {hi:g}{suffix}"


def render_case_card(memory_dir: Path | str, case: str) -> str:
    """Render the plain-text memory card for one case."""
    memory_dir = Path(memory_dir)
    para = _read_case_rows(memory_dir / "parametric_memory.jsonl", case)
    calib = _read_case_rows(memory_dir / "calibration_memory.jsonl", case)
    neg = _read_case_rows(memory_dir / "negative_lessons.jsonl", case)

    title = f"Memory card: {case}"
    out: list[str] = [title, "=" * len(title)]

    if not (para or calib or neg):
        out += [
            "",
            f"No memory recorded for case '{case}' yet.",
            "Run it (with --case-id) and audit a few times to start accumulating signal.",
        ]
        return "\n".join(out)

    # Header
    versions = sorted({str(r["swmm_version"]) for r in para if r.get("swmm_version")})
    dates = sorted(str(r["recorded_utc"]) for r in para if r.get("recorded_utc"))
    header = f"runs recorded: {len(para)}"
    if versions:
        header += "  |  swmm: " + ", ".join(versions)
    if dates:
        header += "  |  last: " + dates[-1]
    out += ["", header]

    # Typical results across the recorded runs
    def qa(row: dict[str, Any], key: str) -> float | None:
        return _num((row.get("qa_metrics") or {}).get(key))

    runoff = [v for v in (qa(r, "runoff_continuity_pct") for r in para) if v is not None]
    flow = [v for v in (qa(r, "flow_continuity_pct") for r in para) if v is not None]
    peaks = [v for v in (qa(r, "peak_flow_value") for r in para) if v is not None]
    if runoff or flow or peaks:
        out += ["", "Typical results"]
        if runoff:
            out.append(f"  runoff continuity:  {_fmt_range(runoff, '%')}")
        if flow:
            out.append(f"  flow continuity:    {_fmt_range(flow, '%')}")
        if peaks:
            nodes = sorted(
                {
                    str((r.get("qa_metrics") or {}).get("peak_flow_node"))
                    for r in para
                    if (r.get("qa_metrics") or {}).get("peak_flow_node")
                }
            )
            node_s = " at " + ", ".join(nodes) if nodes else ""
            out.append(f"  peak flow:          {_fmt_range(peaks, 'CMS')}{node_s}")

    # Run history (most recent first)
    if para:
        out += ["", "Run history (most recent first)"]
        recent = sorted(para, key=lambda r: str(r.get("recorded_utc") or ""), reverse=True)
        for r in recent[:6]:
            qm = r.get("qa_metrics") or {}
            bits: list[str] = []
            if _num(qm.get("runoff_continuity_pct")) is not None:
                bits.append(f"runoff {qm['runoff_continuity_pct']:g}%")
            if _num(qm.get("peak_flow_value")) is not None:
                bits.append(f"peak {qm['peak_flow_value']:g} CMS")
            date = str(r.get("recorded_utc") or "")[:10]
            out.append(f"  {str(r.get('run_id') or '?'):<22} {'  '.join(bits)}   {date}".rstrip())

    # Accepted calibrations
    out += ["", "Accepted calibrations"]
    if calib:
        for r in calib:
            obj = r.get("objective_name") or "objective"
            out.append(f"  {r.get('run_id') or '?'}: {obj} = {r.get('objective_value')}")
    else:
        out.append("  (none yet)")

    # Known-bad parameter regions
    out += ["", "Known-bad regions (avoid)"]
    if neg:
        for r in neg:
            params = r.get("parameters_tried") or {}
            parts = []
            for k, v in params.items():
                parts.append(f"{k}={v:g}" if _num(v) is not None else f"{k}={v}")
            note = str(r.get("note") or "").strip()
            out.append(("  " + ", ".join(parts) + (f"  -> {note}" if note else "")).rstrip())
    else:
        out.append("  (none yet)")

    return "\n".join(out)
