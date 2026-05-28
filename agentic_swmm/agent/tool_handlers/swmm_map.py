"""Network-layout-map handler — typed surface for ``aiswmm map``.

Family: ``swmm-plot`` (shares the renderer skill with ``plot_run`` but
the CLI verb is a sibling of ``aiswmm plot``).

Surfaces the ``map_run`` typed tool so the LLM can pick it directly
from the registry — chaining ``synth_swmm_from_bbox`` →
``run_swmm_inp`` → ``map_run`` in one conversation without resorting
to ``run_allowed_command`` with hand-rolled argv. Mirrors
``demo.py``'s thin-wrapper pattern: build CLI argv, forward to
:func:`_run_cli_tool`. The CLI verb (``agentic_swmm/commands/map.py``)
owns all discovery / validation / matplotlib invocation, so this
module stays small and avoids duplicating the spatial-render policy.

``_run_cli_tool`` resolves ``sys.executable -m agentic_swmm.cli`` so
the subprocess inherits the same interpreter aiswmm is running under
— see ``runtime_env()`` for the PYTHON pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _run_cli_tool
from agentic_swmm.agent.types import ToolCall


def _map_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Render the spatial network map for a run directory.

    Required:
        ``run_dir``: path to a run directory (auto-discovers the INP).

    Optional pass-throughs (matched 1:1 with ``aiswmm map`` flags):
        ``inp``: explicit INP file when discovery should be overridden.
        ``out_png``: explicit output PNG path.
        ``dpi``: positive integer; bad values are silently dropped so
            the CLI's own argparse default takes over.
        ``no_subcatchments``: bool — skip the subcatchment polygon layer.
        ``no_vertices``: bool — draw conduits straight (ignore VERTICES).
    """
    run_dir_raw = call.args.get("run_dir")
    if not isinstance(run_dir_raw, str) or not run_dir_raw.strip():
        return _failure(call, "missing required argument: run_dir")

    cli_args: list[str] = ["map", "--run-dir", run_dir_raw.strip()]

    inp_raw = call.args.get("inp")
    if isinstance(inp_raw, str) and inp_raw.strip():
        cli_args.extend(["--inp", inp_raw.strip()])

    out_png_raw = call.args.get("out_png")
    if isinstance(out_png_raw, str) and out_png_raw.strip():
        cli_args.extend(["--out-png", out_png_raw.strip()])

    dpi_raw = call.args.get("dpi")
    # ``isinstance(True, int)`` is True in Python — guard against bool
    # so the LLM cannot smuggle a flag through the dpi field.
    if isinstance(dpi_raw, int) and not isinstance(dpi_raw, bool) and dpi_raw > 0:
        cli_args.extend(["--dpi", str(dpi_raw)])

    if call.args.get("no_subcatchments"):
        cli_args.append("--no-subcatchments")
    if call.args.get("no_vertices"):
        cli_args.append("--no-vertices")

    return _run_cli_tool(call, session_dir, cli_args)


__all__ = ["_map_run_tool"]
