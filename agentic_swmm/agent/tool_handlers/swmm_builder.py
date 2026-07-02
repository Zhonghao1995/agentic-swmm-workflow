"""SWMM-builder handlers (PRD #128 Phase 2 Group A).

Family: ``swmm-builder``.

Tools that compose a SWMM INP file from CSV/JSON/text inputs via the
``mcp/swmm-builder`` MCP server. Phase 1 (#209) extracted cross-cutting
helpers to ``tool_handlers/_shared``; this module is Phase 2's Group A
slice for the builder family: it owns the ``build_inp`` args mapper and
the factory-built handler that pairs it with
``_make_mcp_routed_handler``.

``_make_mcp_routed_handler`` and the cross-family ``_required_repo_file``
helper still live in ``tool_registry`` (deferred per #211 / reused by
the Group B handlers). They are imported lazily from inside the handler
body and the factory-build helper so the family-module load does not
race the ``tool_registry`` partial-module load.

Cross-cutting helpers (``_failure``, ``_repo_output_path``) come from
``tool_handlers/_shared``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _repo_output_path
from agentic_swmm.agent.types import ToolCall


def _build_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``build_inp`` args to ``swmm-builder.build_inp`` MCP schema."""
    # Lazy import — see module docstring on the circular-load reasoning.
    from agentic_swmm.agent.tool_registry import _required_repo_file

    resolved: dict[str, Path] = {}
    for key, suffix in {"subcatchments_csv": ".csv", "params_json": ".json", "network_json": ".json"}.items():
        path = _required_repo_file(call, key, suffix=suffix)
        if isinstance(path, dict):
            return path
        resolved[key] = path
    out_inp = _repo_output_path(str(call.args["out_inp"]))
    out_manifest = _repo_output_path(str(call.args["out_manifest"]))
    if out_inp is None or out_inp.suffix.lower() != ".inp":
        return _failure(call, "out_inp must be a repository-relative .inp path")
    if out_manifest is None or out_manifest.suffix.lower() != ".json":
        return _failure(call, "out_manifest must be a repository-relative .json path")
    args: dict[str, Any] = {
        "subcatchmentsCsvPath": str(resolved["subcatchments_csv"]),
        "paramsJsonPath": str(resolved["params_json"]),
        "networkJsonPath": str(resolved["network_json"]),
        "outInpPath": str(out_inp),
        "outManifestPath": str(out_manifest),
    }
    optional_paths = {
        "rainfall_json": ("rainfallJsonPath", ".json"),
        "raingage_json": ("raingageJsonPath", ".json"),
        "timeseries_text": ("timeseriesTextPath", None),
        "config_json": ("configJsonPath", ".json"),
        # PRD_water_quality.md PR3: optional WQ config JSON for
        # pollutant buildup/washoff simulation.
        "water_quality_json": ("waterQualityJsonPath", ".json"),
    }
    for snake, (camel, suffix) in optional_paths.items():
        if call.args.get(snake):
            path = _required_repo_file(call, snake, suffix=suffix)
            if isinstance(path, dict):
                return path
            args[camel] = str(path)
    if call.args.get("default_gage_id"):
        args["defaultGageId"] = str(call.args["default_gage_id"])
    return args


def _build_build_inp_tool() -> Any:
    """Construct the ``build_inp`` ToolSpec handler.

    Late-imports ``_make_mcp_routed_handler`` from ``tool_registry`` —
    see module docstring on why module-level imports of that symbol
    are not safe here.
    """
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-builder", "build_inp", args_mapper=_build_inp_args
    )


def __getattr__(name: str) -> Any:
    """Lazily construct ``_build_inp_tool`` on first access.

    The handler cannot be built eagerly at import time: doing so would
    pull ``_make_mcp_routed_handler`` from a still-loading
    ``tool_registry`` module. We defer the factory call to first
    attribute access via the PEP 562 module ``__getattr__`` hook, then
    promote the handler into the module dict so subsequent lookups
    skip the hook entirely (and so that
    ``from ... import _build_inp_tool`` returns a stable, identity-
    preserving object).
    """
    if name == "_build_inp_tool":
        import sys as _sys

        handler = _build_build_inp_tool()
        _sys.modules[__name__].__dict__[name] = handler
        return handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["_build_inp_args", "_build_inp_tool"]
