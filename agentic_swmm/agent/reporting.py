"""``final_report.md`` writer.

PRD_runtime "Module: Report template" rewrites this file. The new
layout has two narrative sections (``## What I did``, ``## What you
got``) and a footer reference to ``agent_trace.jsonl``. The inline
``allowed_tools`` comma-list is dropped; the planner's ``final_text``
still renders verbatim for the user's reading.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ARTIFACT_KIND_LABELS = {
    "input": "Inputs",
    "run": "Run output",
    "plot": "Plots",
    "audit": "Audit",
    "other": "Other artifacts",
}


def write_report(
    session_dir: Path,
    goal: str,
    plan: list[Any],
    results: list[dict[str, Any]],
    *,
    dry_run: bool,
    allowed_tools: set[str],
    planner: str = "rule",
    final_text: str = "",
) -> Path:
    report_path = session_dir / "final_report.md"
    ok = all(result.get("ok") for result in results) if results else dry_run
    status = "DRY RUN" if dry_run else ("PASS" if ok else "FAIL")

    lines: list[str] = [
        "# Agentic SWMM Executor Report",
        "",
        f"- goal: {goal}",
        f"- planner: {planner}",
        f"- status: {status}",
        f"- session_dir: {session_dir}",
        "",
        "## What I did",
        "",
    ]
    did_bullets = _what_i_did(plan, results)
    if did_bullets:
        lines.extend(did_bullets)
    else:
        lines.append("- (no tool calls)")
    lines.extend(["", "## What you got", ""])
    got_lines = _what_you_got(results)
    if got_lines:
        lines.extend(got_lines)
    else:
        lines.append("- (no artifacts)")
    if final_text:
        lines.extend(["", "## Planner Final Answer", "", final_text])
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "This executor only reports commands and artifacts it actually ran or read. A successful SWMM run is not a calibration or validation claim unless observed-data evidence and validation checks are present.",
            "",
            f"_{len(allowed_tools)} tools were available; see `agent_trace.jsonl` for the full call log._",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _what_i_did(plan: list[Any], results: list[dict[str, Any]]) -> list[str]:
    """Render the planner trace as plain-English bullets."""
    bullets: list[str] = []
    for index, call in enumerate(plan, start=1):
        result = results[index - 1] if index - 1 < len(results) else None
        status = "ok" if (result and result.get("ok")) else "skipped"
        if result is None:
            status = "skipped"
        elif not result.get("ok"):
            status = "failed"
        summary = (result or {}).get("summary") or ""
        args = getattr(call, "args", {}) or {}
        args_text = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
        suffix = f" ({summary})" if summary else ""
        head = f"- {index}. `{call.name}`"
        if args_text:
            head += f" {args_text}"
        bullets.append(f"{head} — {status}{suffix}")
    return bullets


def _what_you_got(results: list[dict[str, Any]]) -> list[str]:
    """Group artifact paths by kind for the reader."""
    grouped: dict[str, list[str]] = {}
    for result in results:
        path = result.get("path")
        if not path:
            continue
        kind = _classify_artifact(result, path)
        grouped.setdefault(kind, []).append(str(path))
    if not grouped:
        return []
    lines: list[str] = []
    for kind in ("input", "run", "plot", "audit", "other"):
        if kind not in grouped:
            continue
        lines.append(f"- **{_ARTIFACT_KIND_LABELS[kind]}**")
        for item in grouped[kind]:
            lines.append(f"    - `{item}`")
    return lines


def _classify_artifact(result: dict[str, Any], path: str) -> str:
    tool = str(result.get("tool", "")).lower()
    path_lower = str(path).lower()
    if tool == "plot_run" or path_lower.endswith(".png") or "/07_plots/" in path_lower:
        return "plot"
    if tool == "audit_run" or "/09_audit" in path_lower or "audit_note" in path_lower:
        return "audit"
    if tool == "run_swmm_inp" or path_lower.endswith((".out", ".rpt")):
        return "run"
    if path_lower.endswith((".inp", ".csv", ".json")) or tool in {"read_file", "format_rainfall", "build_inp"}:
        return "input"
    return "other"


def write_event(path: Path, payload: dict[str, Any]) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
