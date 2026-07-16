"""Report-export tool handler.

Family: ``swmm-report``.

Provides the ``generate_report`` direct-subprocess handler that shells out
to ``skills/swmm-report/scripts/generate_report.py``.

Pattern: direct handler (same as audit_run, plot_run) — a thin function
that builds subprocess args and calls ``_run_script_tool``.

The handler catches the script's missing-python-docx non-zero exit and
returns a failure dict whose ``summary`` carries the install hint
(``pip install 'aiswmm[report]'``).
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
from agentic_swmm.utils.paths import repo_root, resource_path

_REPORT_SCRIPT = ("skills", "swmm-report", "scripts", "generate_report.py")


def _generate_report_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Assemble a client-deliverable Word report (.docx) from an audited run directory.

    Shells out to generate_report.py.  Reads manifest.json,
    experiment_provenance.json, model_diagnostics.json, comparison.json,
    and any PNG figures — never re-runs SWMM.  Output path defaults to
    ``<run_dir>/report.docx``.
    """
    run_dir = _resolve_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir

    # Resolve against the source tree OR the installed package (review P1-1);
    # repo_root() alone is source-tree only and fails from a pip-installed wheel.
    try:
        script_path = resource_path(*_REPORT_SCRIPT)
    except FileNotFoundError as exc:
        return _failure(call, str(exc))

    cli_args: list[str] = [str(script_path), "--run-dir", str(run_dir)]

    out_raw = call.args.get("out")
    if isinstance(out_raw, str) and out_raw.strip():
        out_path = Path(out_raw).expanduser()
        if not out_path.is_absolute():
            out_path = (repo_root() / out_path).resolve()
        cli_args.extend(["--out", str(out_path)])

    template_raw = call.args.get("template")
    if isinstance(template_raw, str) and template_raw.strip():
        tmpl_path = Path(template_raw).expanduser()
        if not tmpl_path.is_absolute():
            tmpl_path = (repo_root() / tmpl_path).resolve()
        cli_args.extend(["--template", str(tmpl_path)])

    title_raw = call.args.get("title")
    if isinstance(title_raw, str) and title_raw.strip():
        cli_args.extend(["--title", title_raw])

    result = _run_script_tool(call, session_dir, cli_args)

    # Surface the install hint when python-docx is missing (exit 1, stderr
    # contains the hint text already written by generate_report.py).
    if not result.get("ok") and "python-docx" in (result.get("stderr_tail") or ""):
        result["summary"] = "python-docx not installed; run: pip install 'aiswmm[report]'"

    return result


__all__ = ["_generate_report_tool"]
