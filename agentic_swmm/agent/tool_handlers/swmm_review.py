"""Design-review tool handler.

Family: ``swmm-design-review``.

Provides the ``review_run`` direct-subprocess handler that shells out to
``skills/swmm-design-review/scripts/design_review.py``.

Pattern: identical to ``_audit_run_tool`` — resolve run_dir, build CLI
args, call ``_run_script_tool``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _resolve_run_dir,
    _run_script_tool,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

_REVIEW_SCRIPT = ("skills", "swmm-design-review", "scripts", "design_review.py")


def _review_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Run the deterministic design-review rule checklist against a completed run.

    Shells out to design_review.py.  Writes ``09_review/design_review.json``
    and ``09_review/design_review.md`` into the run directory.  Reports
    findings; never certifies compliance.
    """
    run_dir = _resolve_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir

    script_path = repo_root().joinpath(*_REVIEW_SCRIPT)
    if not script_path.is_file():
        return _failure(call, f"design_review script not found at {script_path}")

    cli_args: list[str] = [str(script_path), "--run-dir", str(run_dir)]

    rules_raw = call.args.get("rules")
    if isinstance(rules_raw, str) and rules_raw.strip():
        rules_path = Path(rules_raw).expanduser()
        if not rules_path.is_absolute():
            rules_path = (repo_root() / rules_path).resolve()
        cli_args.extend(["--rules", str(rules_path)])

    out_dir_raw = call.args.get("out_dir")
    if isinstance(out_dir_raw, str) and out_dir_raw.strip():
        out_dir_path = Path(out_dir_raw).expanduser()
        if not out_dir_path.is_absolute():
            out_dir_path = (repo_root() / out_dir_path).resolve()
        cli_args.extend(["--out-dir", str(out_dir_path)])

    return _run_script_tool(call, session_dir, cli_args)


__all__ = ["_review_run_tool"]
