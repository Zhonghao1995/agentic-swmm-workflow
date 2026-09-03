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


BUNDLED_RULEBOOKS = {
    "gb50014_template": "gb50014_template.yaml",
    "gb50014": "gb50014_template.yaml",
    "synth_plausibility": "synth_plausibility.yaml",
    "plausibility": "synth_plausibility.yaml",
}


def resolve_rulebook(value: str) -> Path:
    """A bundled rulebook name or a YAML path.

    Live finding F-106 (2026-09-03, S47): asked for "the plausibility
    rulebook", the planner had no way to name it and the review ran the
    default GB 50014 template on a Canadian municipal network.
    """
    key = value.strip().lower()
    if key in BUNDLED_RULEBOOKS:
        # resource_path resolves the packaged data dir under a pip install
        # too (the skills-resolution guard forbids repo_root()/skills).
        return Path(resource_path("skills", "swmm-design-review", "rulebooks", BUNDLED_RULEBOOKS[key])).resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (repo_root() / path).resolve()
    return path


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
    rulebook_label = "gb50014_template"
    if isinstance(rules_raw, str) and rules_raw.strip():
        rules_path = resolve_rulebook(rules_raw.strip())
        rulebook_label = rules_path.stem
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
    if result.get("ok"):
        # The summary names the rulebook applied (F-106).
        result = dict(result)
        result["summary"] = f"{str(result.get('summary') or '').rstrip()} (rulebook={rulebook_label})".strip()
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
                    "rules": {
                        "type": "string",
                        "description": (
                            "A bundled rulebook name or a YAML path. Bundled: gb50014_template (the default, a "
                            "standard template whose thresholds must be verified) and synth_plausibility "
                            "(reference-free plausibility screening for synthesized or first-pass networks). "
                            "Use the one the user names."
                        ),
                    },
                    "out_dir": {"type": "string", "description": "Output directory for review artifacts (default: <run_dir>/11_review/)."},
                },
                ["run_dir"],
            ),
            _review_run_tool,
            is_read_only=False,
        ),
    ]
