"""Report-export tool handler.

Family: ``swmm-report``.

Provides the ``generate_report`` direct-subprocess handler that shells out
to ``skills/swmm-report/scripts/generate_report.py``.

Pattern: direct handler (same as audit_run, plot_run) — a thin function
that builds subprocess args and calls ``_run_script_tool``.

The handler catches the script's missing-python-docx non-zero exit and
returns a failure dict whose ``summary`` carries the install hint
(python-docx ships with aiswmm; the remedy is a reinstall).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _resolve_run_dir,
    _run_script_tool,
)
from agentic_swmm.agent.types import ToolCall, ToolSpec
from agentic_swmm.utils.paths import repo_root, resource_path

_REPORT_SCRIPT = ("skills", "swmm-report", "scripts", "generate_report.py")


def house_style_title(text: str) -> str:
    """Return ``text`` with em dashes (and spaced en dashes) turned into colons.

    The planner composes cover titles such as "Downtown Victoria, BC \u2014
    SWMM Simulation Report" (live test 2026-09-03, S34). A deliverable the
    product writes on the user's behalf follows their house style: no em
    dashes, phrases joined by a colon. Unspaced en dashes stay, they are
    numeric ranges ("Nov 1\u20134").
    """
    out = text.replace(" \u2014 ", ": ").replace("\u2014", ": ")
    out = out.replace(" \u2013 ", ": ")
    while "  " in out:
        out = out.replace("  ", " ")
    return out.replace(" :", ":").strip()


BODY_LANGUAGE_NOTE = "body: English report template (only the cover title is free text)"


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
        cli_args.extend(["--title", house_style_title(title_raw)])

    # Extract the .rpt hydraulic tables first. The skill script is stdlib +
    # python-docx + PyYAML only and cannot parse a report file, so it reads
    # this as JSON like every other artifact. Best-effort by design: a run
    # whose .rpt cannot be parsed still gets its report, minus that section.
    try:
        from agentic_swmm.reporting.hydraulic_summary import write_hydraulic_summary

        write_hydraulic_summary(Path(run_dir))
    except Exception:
        pass

    result = _run_script_tool(call, session_dir, cli_args)

    # Surface the install hint when python-docx is missing (exit 1, stderr
    # contains the hint text already written by generate_report.py).
    if not result.get("ok") and "python-docx" in (result.get("stderr_tail") or ""):
        result["summary"] = (
            "python-docx not importable although it ships with aiswmm; reinstall with: "
            "pip install --force-reinstall aiswmm"
        )
    elif result.get("ok"):
        # Asked for a Chinese Word report, the planner announced one while
        # the body was the English template (live test 2026-09-03, S38).
        # The result says what the body is so the final answer can too.
        summary = str(result.get("summary") or "").rstrip()
        result["summary"] = f"{summary} ({BODY_LANGUAGE_NOTE})" if summary else BODY_LANGUAGE_NOTE

    return result


__all__ = ["_generate_report_tool", "tool_specs"]


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 self-registration)."""
    from agentic_swmm.agent.tool_handlers._shared import _object

    return [
        ToolSpec(
            "generate_report",
            "Assemble a client-deliverable Word report (.docx) from an audited run directory. "
            "Reads manifest.json, experiment_provenance.json, model_diagnostics.json, comparison.json, "
            "and any PNG figures — never re-runs SWMM. Output path defaults to <run_dir>/report.docx. "
            "The body follows the English report template whatever language the request is in; "
            "only the cover title is free text, so say so when a report in another language was asked for.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Absolute path to the audited run directory."},
                    "out": {"type": "string", "description": "Output .docx path (default: <run_dir>/report.docx)."},
                    "template": {"type": "string", "description": "Path to a template YAML; omit to use the default template."},
                    "title": {
                        "type": "string",
                        "description": "Override the cover title text. House style: no em dashes; join phrases with a colon.",
                    },
                },
                ["run_dir"],
            ),
            _generate_report_tool,
            is_read_only=False,
        ),
    ]
