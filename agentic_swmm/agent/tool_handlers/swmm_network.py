"""Network QA + INP export handlers (PRD #128 Phase 2 Group B).

Family: ``swmm-network``.

Two handlers extracted from ``tool_registry.py`` as part of Phase 2
Group B of the registry split (Phase 1 — cross-cutting helpers —
landed in PR #209, see ``_shared.py``):

* :func:`_network_qa_tool` — validates a SWMM network JSON via the
  ``swmm-network.qa`` MCP tool.
* :func:`_network_to_inp_tool` — exports a SWMM network JSON to INP
  section text via the ``swmm-network.export_inp`` MCP tool.

Both handlers are MCP-routed. The factory (``_make_mcp_routed_handler``)
and the path-validation helpers (``_required_repo_file``,
``_repo_output_path``) stay in ``tool_registry.py`` for now — the
factory is explicitly deferred to issue #211 because it has a fixture
contract that depends on the registry module. ``_required_repo_file``
is shared across families and still lives in the registry until a
later phase consolidates it into ``_shared.py``.

To avoid the import cycle (``tool_registry`` imports these modules,
and these modules need a helper that lives in ``tool_registry``), the
factory call is wrapped in a lazy build helper and the validation
helpers are imported inside the args mappers — both run only after
``tool_registry`` has bound the helper names. The Phase 2 Group A and
C modules follow the same pattern.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure, _object
from agentic_swmm.agent.types import ToolCall, ToolSpec


def _network_qa_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``network_qa`` args to ``swmm-network.qa`` MCP schema.

    The MCP server's ``qa`` tool only accepts ``networkJsonPath`` — the
    optional ``report_json`` from the ToolSpec surface is ignored; we
    still validate it so the planner gets the same error message it
    used to. (The QA JSON ends up in the MCP server's stdout content.)
    """

    # Lazy import — see module docstring for the cycle rationale.
    from agentic_swmm.agent.tool_registry import (
        _repo_output_path,
        _required_repo_file,
    )

    # Live finding F-67 (2026-09-02): the MCP qa also validates an INP
    # (inpPath); the typed surface only knew network_json, so an INP check
    # through the typed tool failed with "network_json must end with .json".
    has_json = bool(str(call.args.get("network_json") or "").strip())
    has_inp = bool(str(call.args.get("inp_path") or "").strip())
    if has_json == has_inp:
        return _failure(call, "provide exactly one of network_json (a network JSON) or inp_path (a SWMM .inp)")
    if call.args.get("report_json"):
        report = _repo_output_path(str(call.args["report_json"]))
        if report is None or report.suffix.lower() != ".json":
            return _failure(call, "report_json must be a repository-relative .json path")
    if has_inp:
        inp = _required_repo_file(call, "inp_path", suffix=".inp")
        if isinstance(inp, dict):
            return inp
        return {"inpPath": str(inp)}
    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    return {"networkJsonPath": str(network_json)}


def _network_to_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``network_to_inp`` args to ``swmm-network.export_inp`` MCP schema.

    The MCP tool only accepts ``networkJsonPath`` (writes the .inp into a
    tmp directory) — ``out_path`` semantics from the legacy ToolSpec
    surface are preserved by post-processing the MCP response in
    ``_wrap_mcp_result``. The validation here keeps the planner-facing
    error parity.
    """

    from agentic_swmm.agent.tool_registry import (
        _repo_output_path,
        _required_repo_file,
    )

    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    out_path = _repo_output_path(str(call.args["out_path"]))
    if out_path is None or out_path.suffix.lower() not in {".inp", ".txt"}:
        return _failure(call, "out_path must be a repository-relative .inp or .txt path")
    return {"networkJsonPath": str(network_json)}


def _build_handlers() -> tuple[Any, Any]:
    """Build the two MCP-routed handlers for this family.

    Wrapped in a helper so the import edge to ``tool_registry`` is local
    to the call and runs only after the registry's
    ``_make_mcp_routed_handler`` has been bound at the registry-module
    top level. ``tool_registry.py`` imports this module strictly *after*
    defining ``_make_mcp_routed_handler``, so this is safe.
    """

    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return (
        _persist_qa_report(_make_mcp_routed_handler("swmm-network", "qa", args_mapper=_network_qa_args)),
        _make_mcp_routed_handler(
            "swmm-network", "export_inp", args_mapper=_network_to_inp_args
        ),
    )


def _persist_qa_report(routed: Any) -> Any:
    """Write the QA JSON the MCP tool returns to a real file and cite it.

    Live finding F-68 (2026-09-02): the typed tool accepted ``report_json``
    and ignored it, the QA JSON only came back inside the MCP content, and
    the planner then cited the path it had asked for as an artifact that
    did not exist. The report now lands at ``report_json`` when given,
    else at ``<run>/09_audit/network_qa.json``.
    """

    def handler(call: ToolCall, session_dir: Path) -> dict[str, Any]:
        result = routed(call, session_dir)
        if not isinstance(result, dict) or not result.get("ok"):
            return result
        payload = _qa_payload(result.get("results"))
        if payload is None:
            return result
        target = _qa_report_target(call, session_dir)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            result["report_note"] = f"QA report could not be written to {target}: {exc}"
            return result
        result["report_json"] = str(target)
        if isinstance(payload.get("issue_count"), int):
            result["issue_count"] = payload["issue_count"]
        if "ok" in payload:
            result["qa_ok"] = bool(payload["ok"])
        result["summary"] = f"{result.get('summary') or 'network QA'}; report written to {target}"
        return result

    return handler


def _qa_payload(results: Any) -> dict[str, Any] | None:
    content = results.get("content") if isinstance(results, dict) else None
    if not isinstance(content, list):
        return None
    for item in content:
        text = item.get("text") if isinstance(item, dict) else None
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _qa_report_target(call: ToolCall, session_dir: Path) -> Path:
    from agentic_swmm.agent.swmm_runtime import run_layout
    from agentic_swmm.agent.tool_registry import _repo_output_path

    requested = call.args.get("report_json")
    if requested:
        resolved = _repo_output_path(str(requested))
        if resolved is not None:
            return resolved
    return run_layout.stage_dir(Path(session_dir), run_layout.AUDIT) / "network_qa.json"


_network_qa_tool, _network_to_inp_tool = _build_handlers()


__all__ = [
    "_network_qa_args",
    "_network_qa_tool",
    "_network_to_inp_args",
    "_network_to_inp_tool",
    "tool_specs",
]


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 self-registration)."""
    return [
        ToolSpec(
            "network_qa",
            "Network QA (disconnected nodes, missing outfalls, adverse or zero slopes) of a network JSON (network_json) or of an existing SWMM .inp (inp_path); provide exactly one.",
            _object({"network_json": {"type": "string"}, "inp_path": {"type": "string"}, "report_json": {"type": "string"}}, []),
            _network_qa_tool,
        ),
        ToolSpec(
            "network_to_inp",
            "Export a SWMM network JSON to INP section text using the swmm-network script.",
            _object({"network_json": {"type": "string"}, "out_path": {"type": "string"}}, ["network_json", "out_path"]),
            _network_to_inp_tool,
        ),
    ]
