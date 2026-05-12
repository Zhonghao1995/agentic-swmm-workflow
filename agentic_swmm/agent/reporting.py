from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    lines = [
        "# Agentic SWMM Executor Report",
        "",
        f"- goal: {goal}",
        f"- planner: {planner}",
        f"- status: {'DRY RUN' if dry_run else ('PASS' if ok else 'FAIL')}",
        f"- session_dir: {session_dir}",
        f"- allowed_tools: {', '.join(sorted(allowed_tools))}",
        "",
        "## Plan",
        "",
    ]
    for index, call in enumerate(plan, start=1):
        lines.append(f"{index}. `{call.name}` `{json.dumps(call.args, sort_keys=True)}`")
    if results:
        lines.extend(["", "## Tool Results", ""])
        for index, result in enumerate(results, start=1):
            lines.append(f"{index}. `{result['tool']}` - {'OK' if result.get('ok') else 'FAILED'}")
            if result.get("summary"):
                lines.append(f"   - summary: {result['summary']}")
            if result.get("stdout_file"):
                lines.append(f"   - stdout: {result['stdout_file']}")
            if result.get("stderr_file"):
                lines.append(f"   - stderr: {result['stderr_file']}")
            if result.get("path"):
                lines.append(f"   - artifact: {result['path']}")
    if final_text:
        lines.extend(["", "## Planner Final Answer", "", final_text])
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "This executor only reports commands and artifacts it actually ran or read. A successful SWMM run is not a calibration or validation claim unless observed-data evidence and validation checks are present.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def write_event(path: Path, payload: dict[str, Any]) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
