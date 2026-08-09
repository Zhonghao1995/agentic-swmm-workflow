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

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _resolve_run_dir,
    _run_script_tool,
)
from agentic_swmm.agent.types import ToolCall, ToolSpec
from agentic_swmm.utils.paths import repo_root, resource_path

_REVIEW_SCRIPT = ("skills", "swmm-design-review", "scripts", "design_review.py")


def _review_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Run the deterministic design-review rule checklist against a completed run.

    Shells out to design_review.py.  Writes ``11_review/design_review.json``
    and ``11_review/design_review.md`` into the run directory (canonical per
    ADR-0004; older runs may carry these under the legacy ``09_review/``).
    Reports findings; never certifies compliance.
    """
    run_dir = _resolve_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir

    # Resolve against the source tree OR the installed package (review P1-1).
    try:
        script_path = resource_path(*_REVIEW_SCRIPT)
    except FileNotFoundError as exc:
        return _failure(call, str(exc))

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
    else:
        # Canonical default (ADR-0004): always pass --out-dir explicitly so
        # this tool never falls through to design_review.py's own legacy
        # ``09_review`` default.
        out_dir_path = run_layout.stage_dir(run_dir, run_layout.REVIEW)
    cli_args.extend(["--out-dir", str(out_dir_path)])

    result = _run_script_tool(call, session_dir, cli_args)
    # Verdict is DATA, not an execution failure. design_review.py exits
    # 1 when the rulebook verdict is FAIL (correct for `aiswmm review`
    # shell chaining), but for the planner an executed review with a
    # failing verdict is a successful tool call carrying bad news.
    # Before this, every honest FAIL verdict burned one of the failure
    # checkpoint's strikes and pushed sessions toward early stop (found
    # live 2026-08-09 on a wet-window Canada run). Exit codes >= 2
    # remain genuine execution failures.
    if result.get("return_code") == 1:
        stdout_tail = str(result.get("stdout_tail") or "")
        if "Design review:" in stdout_tail:
            verdict_line = next(
                (
                    line.strip()
                    for line in stdout_tail.splitlines()
                    if line.strip().startswith("Design review:")
                ),
                "Design review: FAIL",
            )
            result = dict(result)
            result["ok"] = True
            result["verdict"] = "fail"
            result["summary"] = verdict_line
    return result


__all__ = ["_review_run_tool", "tool_specs"]


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 self-registration)."""
    from agentic_swmm.agent.tool_handlers._shared import _object

    return [
        ToolSpec(
            "review_run",
            "Run the deterministic design-review rule checklist against a completed SWMM run; reports findings, never certifies compliance.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Absolute path to the run directory."},
                    "rules": {"type": "string", "description": "Path to a custom YAML rulebook. Omit to use the bundled GB 50014 template."},
                    "out_dir": {"type": "string", "description": "Output directory for review artifacts (default: <run_dir>/11_review/)."},
                },
                ["run_dir"],
            ),
            _review_run_tool,
            is_read_only=False,
        ),
    ]
