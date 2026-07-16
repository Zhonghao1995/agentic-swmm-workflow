"""Water-quality tool handlers.

Family: ``swmm-water-quality``.

Provides the ``read_wq_loads`` direct-subprocess handler that shells out
to ``skills/swmm-water-quality/scripts/extract_wq_loads.py --rpt <path>``
and returns its JSON.

Pattern: identical to ``_retrieve_memory_tool`` in ``introspection.py`` —
resolve the script path, build CLI args, call ``_run_script_tool``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _run_script_tool,
)
from agentic_swmm.agent.error_remediation import file_resolution_error
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root, resource_path

_WQ_EXTRACT_SCRIPT = ("skills", "swmm-water-quality", "scripts", "extract_wq_loads.py")


def _read_wq_loads_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Shell out to extract_wq_loads.py and return pollutant load JSON.

    Reads the .rpt file at ``rpt_path`` and returns a structured dict with
    WQ section summaries.  When WQ is not enabled in the run, returns
    ``{"ok": True, "wq_present": False}`` without error.
    """
    rpt_path_raw = call.args.get("rpt_path")
    if not isinstance(rpt_path_raw, str) or not rpt_path_raw.strip():
        return _failure(call, "missing required argument: rpt_path")

    rpt_path = Path(rpt_path_raw).expanduser()
    if not rpt_path.is_absolute():
        rpt_path = (repo_root() / rpt_path).resolve()
    if not rpt_path.exists():
        err = file_resolution_error(
            f"rpt file not found: {rpt_path}",
            requested=rpt_path,
            search_dir=rpt_path.parent,
            suffixes=(".rpt",),
        )
        return _failure(call, err.summary, hint=err.hint, cause=err.cause)

    # Resolve against the source tree OR the installed package (review P1-1).
    try:
        script_path = resource_path(*_WQ_EXTRACT_SCRIPT)
    except FileNotFoundError as exc:
        return _failure(call, str(exc))

    cli_args: list[str] = [str(script_path), "--rpt", str(rpt_path)]
    return _run_script_tool(call, session_dir, cli_args)


__all__ = ["_read_wq_loads_tool"]
