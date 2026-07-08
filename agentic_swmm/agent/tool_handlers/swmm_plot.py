"""SWMM-plot handlers (PRD #128 Phase 2 Group A).

Family: ``swmm-plot``.

Tools that render rainfall/runoff figures from a SWMM run. Phase 1
(#209) extracted cross-cutting helpers to ``tool_handlers/_shared``;
this module is Phase 2's Group A slice for the plot family.

Two handler shapes live here:

* ``_plot_run_args`` — the args mapper paired with
  ``_make_mcp_routed_handler`` to invoke the ``mcp/swmm-plot`` MCP
  server's ``plot_rain_runoff_si`` tool. The mapper resolves the run
  dir's INP + OUT via the manifest and synthesises an outPng default
  when the caller did not supply one.
* ``_inspect_plot_options_tool`` — a pure-Python read-only handler
  that inspects a run directory or INP to surface the selectable
  rainfall series, nodes and node-output attributes so the planner
  can choose them explicitly before plotting (see issue #125).

``_make_mcp_routed_handler`` and the cross-family path/INP resolver
helpers (``_required_repo_dir``, ``_resolve_existing_inp``,
``_node_suggestions``, ``_node_attribute_options``) still live in
``tool_registry`` (deferred per #211 / shared across other groups).
They are imported lazily from inside the handler bodies and from the
factory-build helper so the family-module load does not race the
``tool_registry`` partial-module load.

Cross-cutting helpers (``_failure``, ``_repo_path``,
``_repo_output_path``) come from ``tool_handlers/_shared``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _repo_output_path, _repo_path, _resolve_run_dir
from agentic_swmm.agent.error_remediation import file_resolution_error
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.swmm_runtime.inp_parsing import rainfall_timeseries_options
from agentic_swmm.agent.swmm_runtime.run_artifacts import (
    find_inp as _find_inp,
    find_out as _find_out,
    read_manifest as _read_manifest,
)
from agentic_swmm.commands.plot import DEFAULT_NODE_ATTR


def _inspect_plot_options_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    # Lazy import — see module docstring on the circular-load reasoning.
    from agentic_swmm.agent.tool_registry import (
        _node_attribute_options,
        _node_suggestions,
        _resolve_existing_inp,
    )

    run_dir: Path | None = None
    if call.args.get("run_dir"):
        resolved_run_dir = _resolve_run_dir(call, "run_dir")
        if isinstance(resolved_run_dir, dict):
            return resolved_run_dir
        run_dir = resolved_run_dir

    inp: Path | None = None
    if call.args.get("inp_path"):
        inp = _resolve_existing_inp(str(call.args["inp_path"]))
    elif run_dir is not None:
        manifest = _read_manifest(run_dir)
        inp = _find_inp(run_dir, manifest)

    out_file: Path | None = None
    if call.args.get("out_file"):
        out_file = _repo_path(str(call.args["out_file"]))
        if out_file is None or not out_file.exists() or not out_file.is_file():
            err = file_resolution_error(
                f"out_file must be an existing repository file: {call.args['out_file']}",
                requested=call.args["out_file"],
                search_dir=out_file.parent if out_file is not None else None,
                suffixes=(".out",),
            )
            return _failure(call, err.summary, hint=err.hint, cause=err.cause)
    elif run_dir is not None:
        manifest = _read_manifest(run_dir)
        out_file = _find_out(run_dir, manifest)

    rainfall_options = rainfall_timeseries_options(inp) if inp is not None else []
    node_options = _node_suggestions(str(inp), limit=100) if inp is not None else []
    node_attribute_options = _node_attribute_options(out_file, node_options)
    default_rain = next((option["name"] for option in rainfall_options if option.get("used_by_raingage")), None)
    if default_rain is None and rainfall_options:
        default_rain = rainfall_options[0]["name"]
    default_node = node_options[0] if node_options else None

    selections_needed: list[str] = []
    if len(rainfall_options) > 1:
        selections_needed.append("rain_ts")
    if len(node_options) > 1:
        selections_needed.append("node")
    if len(node_attribute_options) > 1:
        selections_needed.append("node_attr")
    user_prompt = ""
    if selections_needed:
        user_prompt = "Please choose " + ", ".join(selections_needed) + " before plotting."

    result = {
        "inp": str(inp) if inp is not None else None,
        "out_file": str(out_file) if out_file is not None else None,
        "rainfall_options": rainfall_options,
        "node_options": node_options,
        "node_attribute_options": node_attribute_options,
        "defaults": {"rain_ts": default_rain, "node": default_node, "node_attr": DEFAULT_NODE_ATTR},
        "selections_needed": selections_needed,
        "user_prompt": user_prompt,
    }
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"rain={len(rainfall_options)} nodes={len(node_options)} attrs={len(node_attribute_options)}"}


def _plot_run_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``plot_run`` args to ``swmm-plot.plot_rain_runoff_si`` MCP schema.

    The MCP server requires ``inp``/``out``/``outPng`` explicitly; the
    legacy CLI handler resolved the first two from the run-dir manifest
    internally. We do the same resolution here so the planner can keep
    passing just ``run_dir``.
    """
    run_dir = _resolve_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    manifest = _read_manifest(run_dir)
    inp_path = _find_inp(run_dir, manifest)
    out_path = _find_out(run_dir, manifest)
    if inp_path is None or not inp_path.is_file():
        err = file_resolution_error(
            f"could not resolve .inp from {run_dir}",
            search_dir=run_dir,
            suffixes=(".inp",),
        )
        return _failure(call, err.summary, hint=err.hint, cause=err.cause)
    if out_path is None or not out_path.is_file():
        err = file_resolution_error(
            f"could not resolve .out from {run_dir}",
            search_dir=run_dir,
            suffixes=(".out",),
        )
        return _failure(call, err.summary, hint=err.hint, cause=err.cause)
    # ``link`` (conduit) is mutually exclusive with ``node`` at the
    # script's argparse layer. When the LLM supplies a link, we must
    # forward it to the MCP and suppress ``node`` so the script doesn't
    # reject the call. If both are supplied (LLM mis-fill), prefer the
    # more-specific ``link`` — silently dropping ``node`` keeps the
    # call valid instead of failing the conduit hydrograph request.
    link_raw = call.args.get("link")
    link = str(link_raw).strip() if isinstance(link_raw, str) and link_raw.strip() else None

    if call.args.get("out_png"):
        out_png = _repo_output_path(str(call.args["out_png"]))
        if out_png is None or out_png.suffix.lower() != ".png":
            return _failure(call, "out_png must be a repository-relative .png path")
    else:
        # The MCP server requires outPng. Match the CLI default (ADR-0004:
        # ``run_layout.PLOT`` = ``08_plot/fig_<node>_<attr>.png`` under the
        # run dir; the legacy ``07_plots`` name stays readable but is never
        # written again). When ``link`` is set, use the conduit id so the
        # file is findable.
        plot_dir = run_layout.stage_dir(run_dir, run_layout.PLOT, create=True)
        if link is not None:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", link).strip("_") or "link"
            out_png = plot_dir / f"fig_link_{safe}_flow.png"
        else:
            node_for_default = re.sub(
                r"[^A-Za-z0-9_.-]+", "_", str(call.args.get("node") or "node")
            ).strip("_") or "node"
            attr_for_default = re.sub(
                r"[^A-Za-z0-9_.-]+", "_", str(call.args.get("node_attr") or "series")
            ).strip("_") or "series"
            out_png = plot_dir / f"fig_{node_for_default}_{attr_for_default}.png"
    args: dict[str, Any] = {
        "inp": str(inp_path),
        "out": str(out_path),
        "outPng": str(out_png),
    }
    if link is not None:
        args["link"] = link
        # node_attr is only meaningful in the node path; the script
        # ignores it when --link is set, and the MCP server branches
        # on the presence of ``link`` to emit --link instead of --node.
    else:
        if call.args.get("node"):
            args["node"] = str(call.args["node"])
        if call.args.get("node_attr"):
            args["nodeAttr"] = str(call.args["node_attr"])
    if call.args.get("rain_ts"):
        args["rainTs"] = str(call.args["rain_ts"])
    else:
        # Issue #327: a bare ``plot_run`` used to reach the script with its
        # "<rainfall-series-name>" placeholder and fail. The planner hop that
        # once injected the choice (``_extract_plot_choice``) no longer
        # exists, so resolve the same default ``inspect_plot_options``
        # reports: the raingage-referenced series, else the first one found.
        # An explicit ``rain_ts`` (the multi-series disambiguation path)
        # still wins above; when the INP has no series at all we forward
        # nothing and the script's own error stays the authority.
        options = rainfall_timeseries_options(inp_path)
        default = next((o for o in options if o.get("used_by_raingage")), None)
        if default is None and options:
            default = options[0]
        if default is not None:
            args["rainTs"] = str(default["name"])
            if default.get("rain_kind"):
                args["rainKind"] = str(default["rain_kind"])
    if call.args.get("rain_kind"):
        args["rainKind"] = str(call.args["rain_kind"])
    # C6 (issue #246): window-cropping plumb-through. The MCP server
    # validates that windowStart/windowEnd require focusDay and rejects
    # them alone; we mirror the constraint in the description only and
    # forward whatever the agent supplies — the server is the authority.
    if call.args.get("focus_day"):
        args["focusDay"] = str(call.args["focus_day"])
    if call.args.get("window_start"):
        args["windowStart"] = str(call.args["window_start"])
    if call.args.get("window_end"):
        args["windowEnd"] = str(call.args["window_end"])
    return args


def _build_plot_run_tool() -> Any:
    """Construct the ``plot_run`` ToolSpec handler.

    Late-imports ``_make_mcp_routed_handler`` from ``tool_registry`` —
    see module docstring on why module-level imports of that symbol
    are not safe here.
    """
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-plot", "plot_rain_runoff_si", args_mapper=_plot_run_args
    )


def __getattr__(name: str) -> Any:
    """Lazily construct ``_plot_run_tool`` on first access.

    See ``swmm_runner.py`` for the rationale behind the lazy-build
    pattern: the factory call needs ``_make_mcp_routed_handler`` from
    ``tool_registry``, which is still mid-load when this module is
    imported by the bottom-of-file re-export statement. Promoting the
    handler into the module dict on first access lets subsequent
    lookups (and the ``from ... import _plot_run_tool`` statement in
    ``tool_registry``) skip the hook with the right identity.
    """
    if name == "_plot_run_tool":
        import sys as _sys

        handler = _build_plot_run_tool()
        _sys.modules[__name__].__dict__[name] = handler
        return handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["_inspect_plot_options_tool", "_plot_run_args", "_plot_run_tool"]
