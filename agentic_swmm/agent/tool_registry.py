from __future__ import annotations

import html
import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent import mcp_cache, mcp_client
from agentic_swmm.agent.mcp_client import McpClientError
from agentic_swmm.agent.mcp_pool import ensure_session_pool
from agentic_swmm.agent.permissions import is_allowed_write_path, is_evidence_path
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.commands.plot import DEFAULT_NODE_ATTR, NODE_ATTRIBUTE_CHOICES, NODE_ATTRIBUTE_LABELS, _find_inp, _find_out, _read_manifest, rainfall_timeseries_options
from agentic_swmm.config import runtime_state_path
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.runtime.registry import discover_skills, load_mcp_registry
from agentic_swmm.utils.paths import repo_root, script_path
from agentic_swmm.utils.subprocess_runner import runtime_env


# CONCURRENCY-OWNER: PRD-GF-CORE
#
# ``supports_gap_fill`` and ``required_file_args`` are the two gap-fill
# (PRD-GF-CORE) hooks on every ToolSpec. Both default to fail-safe
# values so existing tools without explicit opt-in keep their pre-PRD
# behaviour. The handler-wrapping logic that actually intercepts
# ``gap_signal`` lives in ``agentic_swmm.agent.runtime_loop.invoke_tool_with_gap_fill``;
# this file only declares the dataclass fields and routes ``execute``
# through the wrapper.
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolCall, Path], dict[str, Any]]
    # PRD_runtime: ``is_read_only=True`` lets ``Profile.QUICK`` auto-approve
    # the tool without prompting. Default ``False`` is fail-safe — a new
    # tool only joins the auto-approve set when its author explicitly
    # marks it read-only.
    is_read_only: bool = False
    # CONCURRENCY-OWNER: PRD-GF-CORE
    # ``supports_gap_fill=True`` opts the tool into the gap-fill state
    # machine. The runtime then:
    #   * runs the pre-flight L1 scanner over ``required_file_args``
    #     before each invocation,
    #   * intercepts ``{"ok": false, "gap_signal": {...}}`` results
    #     and routes them through the proposer/UI/recorder.
    # Default ``False`` is fail-safe: legacy tools without the flag
    # raise on missing inputs exactly as they did pre-PRD.
    supports_gap_fill: bool = False
    # The tuple of argument names that point at files which must
    # exist before the tool runs. Used by the L1 pre-flight scanner.
    # Empty tuple disables pre-flight scanning (the tool then only
    # surfaces L3 gaps via in-band ``gap_signal``).
    required_file_args: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description, "parameters": self.parameters}


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools = _build_tools()

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def sorted_names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def validate(self, call: ProviderToolCall) -> ToolCall:
        if call.name not in self._tools:
            raise ValueError(f"planner requested unsupported tool: {call.name}")
        return ToolCall(call.name, dict(call.arguments))

    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        spec = self._tools.get(call.name)
        if spec is None:
            return {"tool": call.name, "args": call.args, "ok": False, "summary": f"unsupported tool: {call.name}"}
        # CONCURRENCY-OWNER: PRD-GF-CORE
        # Tools with ``supports_gap_fill=True`` are wrapped through the
        # detect/propose/record/retry state machine. Tools without the
        # opt-in flag bypass the wrapper entirely so existing behaviour
        # is untouched. The wrapper itself is a no-op when neither L1
        # pre-flight nor in-band ``gap_signal`` is in play, so the
        # branch below is cheap.
        if getattr(spec, "supports_gap_fill", False):
            from agentic_swmm.agent.runtime_loop import invoke_tool_with_gap_fill

            return invoke_tool_with_gap_fill(
                spec, call, session_dir, lambda c, sd: spec.handler(c, sd)
            )
        return spec.handler(call, session_dir)

    def is_read_only(self, name: str) -> bool:
        """Return whether ``name`` is a read-only tool.

        Unknown tools fall through to ``False`` — fail-safe.
        """
        spec = self._tools.get(name)
        if spec is None:
            return False
        return bool(spec.is_read_only)

    def describe(self, name: str) -> str | None:
        """Return the ``ToolSpec.description`` for ``name``, or ``None``.

        Used by the UX-3 tool spinner (issue #58) to show the first
        sentence of the description next to the running tool name.
        Unknown tools return ``None`` so callers can fall back to the
        bare tool name.
        """
        spec = self._tools.get(name)
        if spec is None:
            return None
        return spec.description

    def output_for_model(self, result: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {
            "tool",
            "args",
            "ok",
            "return_code",
            "summary",
            "stdout_tail",
            "stderr_tail",
            "path",
            "chars",
            "excerpt",
            "results",
            "servers",
            "tools",
            "mapped_tools",
            "capabilities",
            "recovery",
            "fallback_tools",
            "node_suggestions",
            # PRD-Z `request_expert_review` adds two fields the planner
            # needs to see: ``approved`` (Y/N answer) and ``decision_id``
            # (the ID of the human_decisions record).
            "approved",
            "decision_id",
            # PRD-Y: ``select_skill`` returns the skill's tool subset; the
            # planner needs to see both the tool list and the bound name.
            "skill_name",
            "source",
        }
        return {key: value for key, value in result.items() if key in allowed_keys}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# MCP-routed handler factory (PRD-Y "Handler rewrite — uniform pattern")
# ---------------------------------------------------------------------------
#
# Every deterministic-SWMM ToolSpec's handler is built from this factory so
# the audit trail of "agent → skill → MCP → Python script" is one
# transport. The factory itself is intentionally small: per-tool argument
# mapping (snake_case → camelCase, default-node detection, output-dir
# resolution) lives in dedicated ``_*_mcp_args`` builders below so we keep
# the handler body uniform.


def _make_mcp_routed_handler(
    server: str,
    tool: str,
    *,
    args_mapper: Callable[[ToolCall, Path], dict[str, Any] | dict[str, Any]] | None = None,
) -> Callable[[ToolCall, Path], dict[str, Any]]:
    """Build a ToolSpec handler that forwards the call through ``MCPPool``.

    ``args_mapper`` is an optional pre-call hook that may:
    * translate ToolSpec snake_case argument names into the MCP server's
      camelCase property names,
    * resolve relative paths / inject defaults (e.g. node auto-detect),
    * return a fail-soft result dict early when validation fails — that
      dict is returned verbatim so handlers behave the same way the
      old in-process subprocess handlers did when args were bad.

    The handler returns a flat ``{tool, args, ok, results, summary}``
    dict shaped like the historical subprocess handlers, so existing
    planner / reporting code does not need updating.
    """

    def handler(call: ToolCall, session_dir: Path) -> dict[str, Any]:
        if args_mapper is None:
            mcp_args: dict[str, Any] = dict(call.args)
        else:
            mapped = args_mapper(call, session_dir)
            if isinstance(mapped, dict) and mapped.get("ok") is False and "summary" in mapped:
                # ``_failure``-shaped early return — surface it unchanged.
                return mapped
            mcp_args = mapped if isinstance(mapped, dict) else {}
        pool = ensure_session_pool()
        if pool is None:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": (
                    f"MCP transport unavailable for {server}.{tool}: "
                    "no MCP server registry configured. "
                    "Run: bash scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)."
                ),
            }
        try:
            result = pool.call_tool(server, tool, mcp_args)
        except McpClientError as exc:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": f"MCP transport failed: {exc}",
            }
        return _wrap_mcp_result(call, server, tool, result)

    # Synthetic introspection attribute — the lock-in test
    # ``tests/test_handler_lockin_no_direct_subprocess.py`` reads this to
    # verify that every deterministic-SWMM ToolSpec handler is built via
    # this factory and not a legacy subprocess shim.
    handler._mcp_routing = {"server": server, "tool": tool}  # type: ignore[attr-defined]
    return handler


def _wrap_mcp_result(
    call: ToolCall,
    server: str,
    tool: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Convert the raw MCP ``tools/call`` result into a ToolSpec response.

    The MCP server returns a JSON-RPC ``result`` object — usually with a
    ``content`` array of text blocks. We pass the body through under the
    ``results`` key, and synthesise an ``excerpt`` from the joined text
    blocks so existing reporting code that reads ``stdout_tail`` /
    ``excerpt`` still surfaces useful context to the user.
    """

    excerpt = ""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                if text:
                    chunks.append(text)
        excerpt = "\n".join(chunks)[:4000]
    summary = f"called {server}.{tool}"
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": result,
        "excerpt": excerpt,
        "summary": summary,
    }


def _build_tools() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec("audit_run", "Audit a run directory and write deterministic provenance/comparison/note artifacts.", _object({"run_dir": {"type": "string"}, "workflow_mode": {"type": "string"}, "objective": {"type": "string"}}, ["run_dir"]), _audit_run_tool),
        ToolSpec("apply_patch", "Apply a unified diff patch to repository files. Writes are repo-only and blocked for .git/.venv/secret paths.", _object({"patch": {"type": "string"}, "allow_evidence_edits": {"type": "boolean"}}, ["patch"]), _apply_patch_tool),
        ToolSpec("build_inp", "Assemble a SWMM INP from explicit CSV/JSON/text inputs using the swmm-builder skill.", _object({"subcatchments_csv": {"type": "string"}, "params_json": {"type": "string"}, "network_json": {"type": "string"}, "rainfall_json": {"type": "string"}, "raingage_json": {"type": "string"}, "timeseries_text": {"type": "string"}, "config_json": {"type": "string"}, "default_gage_id": {"type": "string"}, "out_inp": {"type": "string"}, "out_manifest": {"type": "string"}}, ["subcatchments_csv", "params_json", "network_json", "out_inp", "out_manifest"]), _build_inp_tool),
        ToolSpec("capabilities", "Describe what this runtime can and cannot access.", _object({}), _capabilities_tool, is_read_only=True),
        ToolSpec("demo_acceptance", "Run the prepared acceptance demo through the Agentic SWMM CLI.", _object({"run_id": {"type": "string"}, "keep_existing": {"type": "boolean"}}), _demo_acceptance_tool),
        ToolSpec("doctor", "Run the built-in Agentic SWMM runtime doctor.", _object({}), _doctor_tool),
        ToolSpec("format_rainfall", "Format rainfall CSV into SWMM TIMESERIES text and metadata JSON using the swmm-climate skill.", _object({"input_csv": {"type": "string"}, "out_json": {"type": "string"}, "out_timeseries": {"type": "string"}, "series_name": {"type": "string"}, "timestamp_column": {"type": "string"}, "value_column": {"type": "string"}, "value_units": {"type": "string"}, "unit_policy": {"type": "string", "enum": ["strict", "convert_to_mm_per_hr"]}, "timestamp_policy": {"type": "string", "enum": ["strict", "sort"]}}, ["input_csv", "out_json", "out_timeseries"]), _format_rainfall_tool),
        ToolSpec("git_diff", "Read the current repository diff or diff stat.", _object({"stat_only": {"type": "boolean"}, "path": {"type": "string"}}), _git_diff_tool, is_read_only=True),
        ToolSpec("inspect_plot_options", "Inspect a run directory or INP file and return selectable rainfall series, nodes, and node output attributes for plotting.", _object({"run_dir": {"type": "string"}, "inp_path": {"type": "string"}, "out_file": {"type": "string"}}, []), _inspect_plot_options_tool, is_read_only=True),
        ToolSpec("list_dir", "List a repository directory.", _object({"path": {"type": "string"}}), _list_dir_tool, is_read_only=True),
        ToolSpec("list_mcp_servers", "List configured local MCP servers.", _object({}), _list_mcp_servers_tool, is_read_only=True),
        ToolSpec("list_mcp_tools", "List tools exposed by one configured MCP server.", _object({"server": {"type": "string"}, "timeout_seconds": {"type": "integer"}, "refresh": {"type": "boolean"}, "cache_ttl_seconds": {"type": "integer"}}, ["server"]), _list_mcp_tools_tool, is_read_only=True),
        ToolSpec("call_mcp_tool", "Call a tool exposed by a configured local MCP server.", _object({"server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}}, ["server", "tool"]), _call_mcp_tool_tool),
        ToolSpec("list_skills", "List available repository skills.", _object({}), _list_skills_tool, is_read_only=True),
        ToolSpec("network_qa", "Validate a SWMM network JSON using the swmm-network QA script.", _object({"network_json": {"type": "string"}, "report_json": {"type": "string"}}, ["network_json"]), _network_qa_tool),
        ToolSpec("network_to_inp", "Export a SWMM network JSON to INP section text using the swmm-network script.", _object({"network_json": {"type": "string"}, "out_path": {"type": "string"}}, ["network_json", "out_path"]), _network_to_inp_tool),
        ToolSpec("plot_run", "Create a rainfall-runoff plot from a run directory using selected rainfall series, node, and node output attribute.", _object({"run_dir": {"type": "string"}, "node": {"type": "string"}, "node_attr": {"type": "string"}, "rain_ts": {"type": "string"}, "rain_kind": {"type": "string", "enum": ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"]}, "out_png": {"type": "string"}}, ["run_dir"]), _plot_run_tool),
        ToolSpec("read_file", "Read a repository file and return a bounded excerpt.", _object({"path": {"type": "string"}}, ["path"]), _read_file_tool, is_read_only=True),
        ToolSpec("read_skill", "Read a skill contract from skills/<skill_name>/SKILL.md.", _object({"skill_name": {"type": "string"}}, ["skill_name"]), _read_skill_tool, is_read_only=True),
        ToolSpec(
            "recall_memory",
            (
                "Look up the lesson section for an exact failure_pattern name "
                "from memory/modeling-memory/lessons_learned.md.\n"
                "USE WHEN: you know the exact failure_pattern name (e.g. "
                "'peak_flow_parse_missing') and want a precise lookup.\n"
                "DO NOT USE WHEN: user is chatting, or the question is general "
                "(prefer recall_memory_search)."
            ),
            _object({"pattern": {"type": "string"}}, ["pattern"]),
            _recall_memory_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "recall_memory_search",
            (
                "Retrieve the top-k most similar historical entries from the "
                "RAG corpus (memory/rag-memory/) for a natural-language query.\n"
                "USE WHEN: you have a natural-language question or do not know "
                "the failure_pattern name. Returns up to top-k entries with "
                "run_id, source_path, case_name, score, and matched_terms.\n"
                "DO NOT USE WHEN: you have an exact pattern name (prefer "
                "recall_memory) or the question is unrelated to past runs."
            ),
            _object(
                {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                ["query"],
            ),
            _recall_memory_search_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "recall_session_history",
            (
                "Search prior chat sessions in the SQLite session store for relevant past work.\n"
                "USE WHEN: user mentions '上次/昨天/上周/before/previously/continue', or you need "
                "to check whether a similar question / failure pattern has been encountered before.\n"
                "DO NOT USE WHEN: question has no temporal cue and current-session context is sufficient."
            ),
            _object(
                {
                    "query": {"type": "string"},
                    "case_name": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                ["query"],
            ),
            _recall_session_history_tool,
            is_read_only=True,
        ),
        ToolSpec(
            "record_fact",
            (
                "Append a candidate project fact to the staging file for later user review.\n"
                "USE WHEN: user just expressed a durable preference, project convention, or "
                "confirmed fix recipe that future sessions should remember.\n"
                "DO NOT USE WHEN: ephemeral state, file path, secret, or anything you are not "
                "certain the user wants persisted."
            ),
            _object(
                {"text": {"type": "string"}, "source_session_id": {"type": "string"}},
                ["text"],
            ),
            _record_fact_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "request_expert_review",
            (
                "Pause the agent and request expert review.\n"
                "USE WHEN: a QA threshold has been crossed and a "
                "hydrologically consequential decision must be human-approved "
                "before continuing. Pattern must match one of the documented "
                "HITL thresholds (see docs/hitl-thresholds.md).\n"
                "DO NOT USE WHEN: low-stakes confirmation or routine reasoning."
            ),
            _object(
                {
                    "run_dir": {"type": "string"},
                    "pattern": {"type": "string"},
                    "evidence_ref": {"type": "string"},
                    "message": {"type": "string"},
                },
                ["run_dir", "pattern", "evidence_ref", "message"],
            ),
            _request_expert_review_tool,
            # is_read_only=False — QUICK profile must NEVER auto-approve
            # the HITL pause (PRD-Z hard requirement).
            is_read_only=False,
        ),
        ToolSpec("run_swmm_inp", "Run a repository or imported external .inp file through the constrained swmm-runner CLI wrapper.", _object({"inp_path": {"type": "string"}, "run_id": {"type": "string"}, "run_dir": {"type": "string"}, "node": {"type": "string"}}, ["inp_path"]), _run_swmm_inp_tool),
        ToolSpec("run_allowed_command", "Run an allowlisted local command such as pytest, python -m agentic_swmm.cli, node scripts/*.mjs, or swmm5.", _object({"command": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}, ["command"]), _run_allowed_command_tool),
        ToolSpec("run_tests", "Run pytest on selected repository test paths.", _object({"paths": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}), _run_tests_tool),
        ToolSpec("search_files", "Search text files in the repository.", _object({"query": {"type": "string"}, "glob": {"type": "string"}, "max_results": {"type": "integer"}}), _search_files_tool, is_read_only=True),
        ToolSpec(
            "select_skill",
            (
                "Commit to a workflow skill and receive its full tool list.\n"
                "USE WHEN: you are about to invoke a deterministic SWMM operation "
                "and have identified which skill provides it (e.g. swmm-builder, "
                "swmm-runner, swmm-plot). The response gives you the skill's "
                "tools (name + description + parameters); pick one and call it next.\n"
                "DO NOT USE WHEN: you only need agent-internal tools (memory recall, "
                "workflow mode selection, plot option inspection, file / dir / git "
                "inspection). Those are always available without selecting a skill."
            ),
            _object({"skill_name": {"type": "string"}}, ["skill_name"]),
            _select_skill_tool,
            is_read_only=True,
        ),
        ToolSpec("select_workflow_mode", "Select the top-level swmm-end-to-end operating mode and report required/missing inputs before running tools.", _object({"goal": {"type": "string"}, "inp_path": {"type": "string"}, "run_dir": {"type": "string"}, "node": {"type": "string"}, "network_json": {"type": "string"}, "subcatchments_csv": {"type": "string"}, "rainfall_input": {"type": "string"}, "landuse_input": {"type": "string"}, "soil_input": {"type": "string"}, "observed_flow": {"type": "string"}, "fuzzy_config": {"type": "string"}, "baseline_run_dir": {"type": "string"}}, ["goal"]), _select_workflow_mode_tool, is_read_only=True),
        ToolSpec("summarize_memory", "Summarize audited runs into the modeling-memory directory.", _object({"runs_dir": {"type": "string"}, "out_dir": {"type": "string"}}, ["runs_dir"]), _summarize_memory_tool),
        ToolSpec("web_fetch_url", "Fetch and summarize a web page. Web evidence is not SWMM run evidence.", _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}), _web_fetch_url_tool, is_read_only=True),
        ToolSpec("web_search", "Run a lightweight web search and return cited result URLs. Web evidence is not SWMM run evidence.", _object({"query": {"type": "string"}, "allowed_domains": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}}), _web_search_tool, is_read_only=True),
    ]
    return {spec.name: spec for spec in specs}


def _doctor_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    return _run_cli_tool(call, session_dir, ["doctor"])


def _request_expert_review_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Thin shim around :func:`agentic_swmm.hitl.request_expert_review`.

    The real handler lives in the ``hitl`` package so the tool wiring
    and the pause/prompt/record logic can evolve independently. The
    shim is the integration seam the registry calls through.
    """
    from agentic_swmm.hitl.request_expert_review import request_expert_review

    return request_expert_review(call, session_dir)


def _apply_patch_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    patch = str(call.args.get("patch") or "")
    if not patch.strip():
        return _failure(call, "patch is required")
    touched = _patch_paths(patch)
    if not touched:
        return _failure(call, "patch did not contain recognizable file paths")
    allow_evidence = bool(call.args.get("allow_evidence_edits"))
    for path in touched:
        full = _repo_path(path)
        if full is None:
            return _failure(call, f"patch path must be inside repository: {path}")
        if not is_allowed_write_path(full):
            return _failure(call, f"patch path is blocked by policy: {path}")
        if is_evidence_path(full) and not allow_evidence:
            return _failure(call, f"patch modifies evidence/generated memory path; set allow_evidence_edits only for explicit regenerate tasks: {path}")
    patch_path = session_dir / "tool_results" / "apply_patch.diff"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch, encoding="utf-8")
    proc = subprocess.run(["git", "apply", str(patch_path)], cwd=repo_root(), capture_output=True, text=True)
    return {
        "tool": call.name,
        "args": {"path_count": len(touched), "allow_evidence_edits": allow_evidence},
        "ok": proc.returncode == 0,
        "return_code": proc.returncode,
        "path": str(patch_path),
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
        "summary": f"applied patch to {len(touched)} path(s)" if proc.returncode == 0 else "patch failed",
    }


def _demo_acceptance_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["demo", "acceptance", "--run-id", str(call.args.get("run_id", "agent-latest"))]
    if call.args.get("keep_existing"):
        command.append("--keep-existing")
    return _run_cli_tool(call, session_dir, command)


# ---------------------------------------------------------------------------
# Deterministic-SWMM handlers — MCP-routed (PRD-Y)
# ---------------------------------------------------------------------------
#
# Each handler below is the output of ``_make_mcp_routed_handler`` paired
# with a per-tool ``_*_args_mapper`` function. The mapper:
#   1. validates required snake_case arguments (paths exist / are in-repo
#      / have the right suffix) and returns a ``_failure`` dict on
#      problems so the planner sees the same fail-soft shape it always
#      did,
#   2. translates snake_case ToolSpec argument names into the camelCase
#      property names each MCP server expects (mirrors
#      ``mcp/<server>/server.js`` schemas).


def _audit_run_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``audit_run`` args to ``swmm-experiment-audit`` MCP schema."""

    run_dir = call.args.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.strip():
        return _failure(call, "missing required argument: run_dir")
    args: dict[str, Any] = {"runDir": run_dir}
    if call.args.get("workflow_mode"):
        args["workflowMode"] = str(call.args["workflow_mode"])
    if call.args.get("objective"):
        args["objective"] = str(call.args["objective"])
    return args


_audit_run_tool = _make_mcp_routed_handler(
    "swmm-experiment-audit", "audit_run", args_mapper=_audit_run_args
)


def _summarize_memory_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``summarize_memory`` args to ``swmm-modeling-memory`` MCP schema.

    The MCP server requires both ``runsDir`` and ``outDir``; if the caller
    omits ``out_dir`` we default to ``memory/modeling-memory`` (the same
    default the CLI used).
    """

    runs_dir = call.args.get("runs_dir")
    if not isinstance(runs_dir, str) or not runs_dir.strip():
        return _failure(call, "missing required argument: runs_dir")
    out_dir = call.args.get("out_dir") or "memory/modeling-memory"
    return {"runsDir": str(runs_dir), "outDir": str(out_dir)}


_summarize_memory_tool = _make_mcp_routed_handler(
    "swmm-modeling-memory", "summarize_memory", args_mapper=_summarize_memory_args
)


# -- Memory recall tools (PRD M1, M6, M7.1) -----------------------------------


_RECALL_PATTERN_TOKEN_BUDGET = 500
_RECALL_SEARCH_TOKEN_BUDGET = 1000


def _estimated_tokens(text: str) -> int:
    """Cheap word/4 token estimator; PRD uses this same heuristic for limits."""
    return max(1, len(text.split())) if text else 0


def _truncate_to_token_budget(text: str, budget: int) -> str:
    if not text:
        return text
    if _estimated_tokens(text) <= budget:
        return text
    words = text.split()
    return " ".join(words[: max(1, budget)]) + "\n...[truncated]"


def _lessons_path() -> Path:
    """Resolve the curated lessons file path.

    Reads from ``AISWMM_LESSONS_PATH`` when set (tests use this); otherwise
    falls back to the runtime memory registry record so that Memory and
    Runtime share one source of truth.
    """
    import os as _os

    override = _os.environ.get("AISWMM_LESSONS_PATH")
    if override:
        return Path(override)
    return repo_root() / "memory" / "modeling-memory" / "lessons_learned.md"


def _rag_index_dir() -> Path:
    import os as _os

    override = _os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override)
    return repo_root() / "memory" / "rag-memory"


def _recall_memory_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence
    from agentic_swmm.memory.recall import recall as _recall

    pattern = str(call.args.get("pattern") or "").strip()
    if not pattern:
        return _failure(call, "pattern is required")

    section = _recall(pattern, _lessons_path())
    if not section:
        wrapped = _wrap_fence("", source="lessons", stale=False)
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": {"pattern": pattern, "layer": "curated", "found": False},
            "excerpt": wrapped,
            "chars": len(wrapped),
            "summary": f"recall_memory: no match for pattern '{pattern}'",
        }

    truncated = _truncate_to_token_budget(section, _RECALL_PATTERN_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="lessons", stale=False)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": {"pattern": pattern, "layer": "curated", "found": True},
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_memory: matched '{pattern}' ({_estimated_tokens(truncated)} est. tokens)",
    }


def _recall_memory_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence

    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    top_k = int(call.args.get("top_k") or 3)

    try:
        from agentic_swmm.memory.recall_search import recall_search as _recall_search
    except Exception as exc:
        return _failure(call, f"recall_memory_search backend unavailable: {exc}")

    index_dir = _rag_index_dir()
    corpus_path = index_dir / "corpus.jsonl"
    lessons_path = _lessons_path()

    try:
        results = _recall_search(
            query,
            top_k=top_k,
            index_dir=index_dir,
            corpus_path=corpus_path,
            lessons_path=lessons_path,
        )
    except Exception as exc:
        return _failure(call, f"recall_memory_search failed: {exc}")

    stale = any(bool(result.get("warning")) for result in results)
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    truncated = _truncate_to_token_budget(rendered, _RECALL_SEARCH_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="rag", stale=stale)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": results,
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_memory_search: {len(results)} hit(s) for query (stale={stale})",
    }


_RECALL_SESSION_HISTORY_TOKEN_BUDGET = 1000


def _recall_session_history_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Search prior chat sessions in the cross-session SQLite store.

    The handler returns its payload wrapped in a ``<memory-context>``
    fence (source ``"sessions"``) so the planner's prompt-injection
    defences extend automatically to this new layer.
    """
    from agentic_swmm.memory import session_db as _session_db
    from agentic_swmm.memory.context_fence import wrap as _wrap_fence
    from agentic_swmm.memory.session_sync import default_db_path

    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    case_name = call.args.get("case_name")
    case_name = str(case_name).strip() if isinstance(case_name, str) and case_name.strip() else None
    limit = int(call.args.get("limit") or 5)

    db_path = default_db_path()
    if not db_path.exists():
        wrapped = _wrap_fence("(no prior sessions recorded)", source="sessions", stale=False)
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "results": [],
            "excerpt": wrapped,
            "chars": len(wrapped),
            "summary": "recall_session_history: store not initialised yet",
        }

    try:
        with _session_db.connect(db_path) as conn:
            hits = _session_db.search_messages(
                conn, query, case_name=case_name, limit=limit
            )
    except Exception as exc:
        return _failure(call, f"recall_session_history failed: {exc}")

    rendered = json.dumps(hits, ensure_ascii=False, indent=2)
    truncated = _truncate_to_token_budget(rendered, _RECALL_SESSION_HISTORY_TOKEN_BUDGET)
    wrapped = _wrap_fence(truncated, source="sessions", stale=False)
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "results": hits,
        "excerpt": wrapped,
        "chars": len(wrapped),
        "summary": f"recall_session_history: {len(hits)} session(s) matched query",
    }


def _record_fact_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Append a candidate fact block to ``facts_staging.md``.

    This is the only write path the LLM has into the facts layer; the
    user promotes from staging into ``facts.md`` manually via the
    ``aiswmm memory promote-facts`` CLI. Marking the tool ``is_read_only=False``
    keeps it out of ``Profile.QUICK`` auto-approve.
    """
    from agentic_swmm.memory import facts as _facts_mod

    text = str(call.args.get("text") or "").strip()
    if not text:
        return _failure(call, "text is required")
    source_id = call.args.get("source_session_id")
    source_id = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    try:
        staging_path = _facts_mod.record_fact_to_staging(
            text, source_session_id=source_id
        )
    except Exception as exc:
        return _failure(call, f"record_fact failed: {exc}")
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "path": str(staging_path),
        "summary": "fact appended to staging; run `aiswmm memory promote-facts` to review",
    }


def _read_file_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args["path"]))
    if path is None:
        return _failure(call, "refusing to read outside repository")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": text[:4000], "summary": f"read {path.relative_to(repo_root())}"}


def _list_skills_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skills = [{"name": str(r.get("name")), "enabled": bool(r.get("enabled", True)), "path": str(r.get("path"))} for r in discover_skills()]
    return {"tool": call.name, "args": call.args, "ok": True, "skills": skills, "summary": f"{len(skills)} skills available", "excerpt": json.dumps(skills, indent=2)[:4000]}


def _read_skill_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skill_name = str(call.args["skill_name"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", skill_name):
        return _failure(call, "invalid skill name")
    path = _repo_path(f"skills/{skill_name}/SKILL.md")
    if path is None or not path.exists() or not path.is_file():
        return _failure(call, f"skill not found: {skill_name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": text[:4000], "summary": f"read skill {skill_name}"}


def _run_swmm_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``run_swmm_inp`` args to ``swmm-runner.swmm_run`` MCP schema.

    Path validation (in-repo + suffix) and default ``run_dir`` / ``node``
    selection mirror the historical in-process handler so behaviour is
    identical for the caller.
    """

    inp = _resolve_inp_for_run(call)
    if isinstance(inp, dict):
        return inp
    run_dir = _optional_repo_output_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    if run_dir is None:
        run_id = str(call.args.get("run_id") or f"{_safe_name(inp.stem)}-{int(time.time())}")
        run_dir = repo_root() / "runs" / "agent" / _safe_name(run_id)
    default_node = _node_suggestions(str(inp), limit=1)
    node = str(call.args.get("node") or (default_node[0] if default_node else "O1"))
    return {"inp": str(inp), "runDir": str(run_dir), "node": node}


_run_swmm_inp_tool = _make_mcp_routed_handler(
    "swmm-runner", "swmm_run", args_mapper=_run_swmm_inp_args
)


def _inspect_plot_options_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    run_dir: Path | None = None
    if call.args.get("run_dir"):
        resolved_run_dir = _required_repo_dir(call, "run_dir")
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
            return _failure(call, f"out_file must be an existing repository file: {call.args['out_file']}")
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


def _select_skill_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Return the skill's tool subset (PRD-Y two-level planner surface).

    Lazy-imports ``SkillRouter`` because ``skill_router`` already imports
    from ``tool_registry`` — keeping the import inside the handler avoids
    a load-time cycle.
    """

    from agentic_swmm.agent.skill_router import AGENT_INTERNAL_SKILL, SkillRouter

    skill_name = call.args.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return _failure(call, "skill_name is required")
    skill_name = skill_name.strip()
    router = SkillRouter(AgentToolRegistry())
    try:
        bundle = router.tools_for(skill_name)
    except KeyError:
        known = ", ".join(router.list_skills())
        return _failure(call, f"unknown skill: {skill_name} (known: {known})")
    entries = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "is_read_only": bool(tool.is_read_only),
        }
        for tool in bundle.tools
    ]
    summary = (
        f"selected skill {skill_name}: {len(entries)} tool(s) "
        f"({bundle.source})"
    )
    if skill_name == AGENT_INTERNAL_SKILL:
        summary += " — available without further select_skill calls"
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "skill_name": skill_name,
        "source": bundle.source,
        "tools": entries,
        "summary": summary,
    }


def _select_workflow_mode_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    goal = str(call.args.get("goal") or "").lower()
    provided = {key: str(value).strip() for key, value in call.args.items() if isinstance(value, str) and value.strip()}

    # NOTE: Chinese keywords are part of the bilingual goal-routing contract.
    # The `??` placeholders previously here were a non-UTF-8 sync regression
    # (P0-2 in #79). Do not replace these with ASCII placeholders again — the
    # regression test in `tests/test_select_workflow_mode_bilingual.py` will
    # trip on any reintroduced `??` pair.
    wants_calibration = any(word in goal for word in ("calibration", "calibrate", "observed", "nse", "kge", "校准", "率定"))
    wants_uncertainty = any(word in goal for word in ("uncertainty", "fuzzy", "sensitivity", "不确定性", "敏感性"))
    wants_audit = "audit" in goal or "comparison" in goal or "compare" in goal or "审计" in goal or "比较" in goal
    wants_plot = any(
        word in goal
        for word in (
            "plot",
            "figure",
            "graph",
            "作图",
            "画图",
            "图",
            "rainfall",
            "node",
            "outfall",
            "total_inflow",
            "lateral_inflow",
            "flow_lost_flooding",
            "volume_stored_ponded",
            "depth_above_invert",
            "hydraulic_head",
        )
    )
    wants_demo = any(word in goal for word in ("demo", "acceptance", "演示", "验收"))
    has_inp = bool(provided.get("inp_path"))
    has_run_dir = bool(provided.get("run_dir"))
    if (wants_plot or wants_audit) and not has_run_dir:
        active_run_dir = _active_run_dir_from_global_state()
        if active_run_dir:
            provided["run_dir"] = active_run_dir
            has_run_dir = True
    full_build_inputs = ["network_json", "subcatchments_csv", "rainfall_input", "landuse_input", "soil_input"]
    has_full_build = all(provided.get(key) for key in full_build_inputs)

    if wants_plot and has_run_dir:
        mode = "existing_run_plot"
        required = ["run_dir"]
        next_tools = ["inspect_plot_options", "plot_run"]
        boundary = "Plots generated from an existing run directory are visualization evidence from recorded SWMM artifacts."
    elif wants_calibration:
        mode = "calibration"
        required = ["inp_path", "observed_flow", "node"]
        next_tools = ["run_swmm_inp", "audit_run"]
        boundary = "Calibration requires observed flow evidence and recorded parameter-selection artifacts; a successful run alone is not calibration."
    elif wants_uncertainty:
        mode = "uncertainty"
        required = ["inp_path", "fuzzy_config", "node"]
        next_tools = ["run_swmm_inp", "audit_run"]
        boundary = "Uncertainty runs produce scenario evidence, not calibrated predictive uncertainty unless supported by observed-data validation."
    elif has_inp:
        mode = "prepared_inp_cli"
        required = ["inp_path"]
        next_tools = ["run_swmm_inp", "audit_run", "inspect_plot_options", "plot_run"]
        boundary = "Prepared INP execution is runnable/checkable/auditable evidence, not calibration or validation by itself."
    elif wants_demo:
        mode = "prepared_demo"
        required = []
        next_tools = ["demo_acceptance", "audit_run"]
        boundary = "Prepared demos are smoke or benchmark evidence, not proof of arbitrary greenfield modeling."
    elif wants_audit and not has_inp:
        mode = "audit_only_or_comparison"
        required = ["run_dir"]
        if "compare" in goal or "comparison" in goal or "比较" in goal:
            required.append("baseline_run_dir")
        next_tools = ["audit_run"]
        boundary = "Audit records existing artifacts; it does not create missing SWMM execution evidence."
    elif has_full_build:
        mode = "full_modular_build"
        required = full_build_inputs
        next_tools = ["format_rainfall", "network_qa", "build_inp", "run_swmm_inp", "audit_run"]
        boundary = "Full modular build requires explicit GIS/network/rainfall/parameter inputs; the agent must not invent missing model inputs."
    else:
        mode = "needs_user_inputs"
        required = ["inp_path or full modular build inputs"]
        next_tools = []
        boundary = "No SWMM execution should start until a prepared INP or complete build inputs are provided."

    missing = [item for item in required if item not in provided]
    if "inp_path or full modular build inputs" in required:
        missing = ["SWMM INP path, or network_json + subcatchments_csv + rainfall_input + landuse_input + soil_input"]
    if mode == "prepared_demo":
        missing = []

    node_suggestions = _node_suggestions(provided.get("inp_path"))
    plot_selection_options = _plot_selection_options_for_inp(provided.get("inp_path"))
    result = {
        "mode": mode,
        "top_level_contract": "skills/swmm-end-to-end/SKILL.md",
        "required_inputs": required,
        "provided_inputs": sorted(provided),
        "provided_values": provided,
        "missing_inputs": missing,
        "recommended_next_tools": [] if missing else next_tools,
        "stop_reason": "missing critical input" if missing else None,
        "evidence_boundary": boundary,
        "user_prompt": _workflow_user_prompt(mode, missing),
        "node_suggestions": node_suggestions,
        "plot_selection_options": plot_selection_options,
    }
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"mode={mode} missing={len(missing)}"}


def _plot_run_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``plot_run`` args to ``swmm-plot.plot_rain_runoff_si`` MCP schema.

    The MCP server requires ``inp``/``out``/``outPng`` explicitly; the
    legacy CLI handler resolved the first two from the run-dir manifest
    internally. We do the same resolution here so the planner can keep
    passing just ``run_dir``.
    """

    run_dir = _required_repo_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    manifest = _read_manifest(run_dir)
    inp_path = _find_inp(run_dir, manifest)
    out_path = _find_out(run_dir, manifest)
    if inp_path is None or not inp_path.is_file():
        return _failure(call, f"could not resolve .inp from {run_dir}")
    if out_path is None or not out_path.is_file():
        return _failure(call, f"could not resolve .out from {run_dir}")
    if call.args.get("out_png"):
        out_png = _repo_output_path(str(call.args["out_png"]))
        if out_png is None or out_png.suffix.lower() != ".png":
            return _failure(call, "out_png must be a repository-relative .png path")
    else:
        # The MCP server requires outPng. Match the historical CLI default
        # (``07_plots/fig_<node>_<attr>.png`` under the run dir).
        node_for_default = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", str(call.args.get("node") or "node")
        ).strip("_") or "node"
        attr_for_default = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", str(call.args.get("node_attr") or "series")
        ).strip("_") or "series"
        out_png = run_dir / "07_plots" / f"fig_{node_for_default}_{attr_for_default}.png"
        out_png.parent.mkdir(parents=True, exist_ok=True)
    args: dict[str, Any] = {
        "inp": str(inp_path),
        "out": str(out_path),
        "outPng": str(out_png),
    }
    if call.args.get("node"):
        args["node"] = str(call.args["node"])
    if call.args.get("node_attr"):
        args["nodeAttr"] = str(call.args["node_attr"])
    if call.args.get("rain_ts"):
        args["rainTs"] = str(call.args["rain_ts"])
    if call.args.get("rain_kind"):
        args["rainKind"] = str(call.args["rain_kind"])
    return args


_plot_run_tool = _make_mcp_routed_handler(
    "swmm-plot", "plot_rain_runoff_si", args_mapper=_plot_run_args
)


def _network_qa_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``network_qa`` args to ``swmm-network.qa`` MCP schema.

    The MCP server's ``qa`` tool only accepts ``networkJsonPath`` — the
    optional ``report_json`` from the ToolSpec surface is ignored; we
    still validate it so the planner gets the same error message it
    used to. (The QA JSON ends up in the MCP server's stdout content.)
    """

    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    if call.args.get("report_json"):
        report = _repo_output_path(str(call.args["report_json"]))
        if report is None or report.suffix.lower() != ".json":
            return _failure(call, "report_json must be a repository-relative .json path")
    return {"networkJsonPath": str(network_json)}


_network_qa_tool = _make_mcp_routed_handler(
    "swmm-network", "qa", args_mapper=_network_qa_args
)


def _network_to_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``network_to_inp`` args to ``swmm-network.export_inp`` MCP schema.

    The MCP tool only accepts ``networkJsonPath`` (writes the .inp into a
    tmp directory) — ``out_path`` semantics from the legacy ToolSpec
    surface are preserved by post-processing the MCP response in
    ``_wrap_mcp_result``. The validation here keeps the planner-facing
    error parity.
    """

    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    out_path = _repo_output_path(str(call.args["out_path"]))
    if out_path is None or out_path.suffix.lower() not in {".inp", ".txt"}:
        return _failure(call, "out_path must be a repository-relative .inp or .txt path")
    return {"networkJsonPath": str(network_json)}


_network_to_inp_tool = _make_mcp_routed_handler(
    "swmm-network", "export_inp", args_mapper=_network_to_inp_args
)


def _format_rainfall_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``format_rainfall`` args to ``swmm-climate.format_rainfall`` MCP."""

    input_csv = _required_repo_file(call, "input_csv", suffix=".csv")
    if isinstance(input_csv, dict):
        return input_csv
    out_json = _repo_output_path(str(call.args["out_json"]))
    out_timeseries = _repo_output_path(str(call.args["out_timeseries"]))
    if out_json is None or out_json.suffix.lower() != ".json":
        return _failure(call, "out_json must be a repository-relative .json path")
    if out_timeseries is None or out_timeseries.suffix.lower() not in {".txt", ".dat"}:
        return _failure(call, "out_timeseries must be a repository-relative .txt or .dat path")
    args: dict[str, Any] = {
        "inputCsvPath": str(input_csv),
        "outputJsonPath": str(out_json),
        "outputTimeseriesPath": str(out_timeseries),
    }
    snake_to_camel = {
        "series_name": "seriesName",
        "timestamp_column": "timestampColumn",
        "value_column": "valueColumn",
        "value_units": "valueUnits",
        "unit_policy": "unitPolicy",
        "timestamp_policy": "timestampPolicy",
    }
    for snake, camel in snake_to_camel.items():
        if call.args.get(snake):
            args[camel] = str(call.args[snake])
    return args


_format_rainfall_tool = _make_mcp_routed_handler(
    "swmm-climate", "format_rainfall", args_mapper=_format_rainfall_args
)


def _build_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``build_inp`` args to ``swmm-builder.build_inp`` MCP schema."""

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


_build_inp_tool = _make_mcp_routed_handler(
    "swmm-builder", "build_inp", args_mapper=_build_inp_args
)


def _list_dir_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args.get("path") or "."))
    if path is None or not path.exists() or not path.is_dir():
        return _failure(call, "directory must exist inside repository")
    entries = [{"name": item.name, "type": "dir" if item.is_dir() else "file", "path": str(item.relative_to(repo_root()))} for item in sorted(path.iterdir())[:200]]
    return {"tool": call.name, "args": call.args, "ok": True, "results": entries, "summary": f"{len(entries)} entries"}


def _search_files_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    pattern = _normalize_search_glob(str(call.args.get("glob") or "*"))
    max_results = int(call.args.get("max_results") or 50)
    results: list[dict[str, Any]] = []
    try:
        paths = repo_root().rglob(pattern)
    except ValueError as exc:
        return _failure(call, f"invalid glob pattern: {exc}")
    for path in paths:
        if len(results) >= max_results:
            break
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({"path": str(path.relative_to(repo_root())), "line": lineno, "text": line.strip()[:300]})
                break
    return {"tool": call.name, "args": call.args, "ok": True, "glob": pattern, "results": results, "summary": f"{len(results)} match(es)"}


def _normalize_search_glob(pattern: str) -> str:
    cleaned = pattern.strip() or "*"
    # pathlib requires "**" to be a complete path component. LLM planners often
    # produce "**.inp" when they mean a recursive extension search.
    cleaned = re.sub(r"(?<!/)\*\*\.([A-Za-z0-9_*?[\]-]+)$", r"**/*.\1", cleaned)
    cleaned = re.sub(r"/\*\*\.([A-Za-z0-9_*?[\]-]+)$", r"/**/*.\1", cleaned)
    return cleaned


def _git_diff_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["git", "diff", "--stat" if call.args.get("stat_only", True) else "--"]
    if call.args.get("path"):
        command.extend(["--", str(call.args["path"])])
    proc = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True)
    return {"tool": call.name, "args": call.args, "ok": proc.returncode == 0, "return_code": proc.returncode, "excerpt": proc.stdout[:8000], "stderr_tail": _tail(proc.stderr), "summary": "git diff read" if proc.returncode == 0 else "git diff failed"}


def _web_fetch_url_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    url = str(call.args.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _failure(call, "url must be http(s)")
    max_chars = int(call.args.get("max_chars") or 6000)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web fetch failed: {exc}")
    text = _strip_html(raw)
    return {"tool": call.name, "args": call.args, "ok": True, "path": url, "chars": len(text), "excerpt": text[:max_chars], "summary": f"fetched {url}"}


def _web_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    max_results = min(int(call.args.get("max_results") or 5), 10)
    allowed = [str(domain).lower() for domain in call.args.get("allowed_domains") or []]
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web search failed: {exc}")
    results = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        href = html.unescape(match.group(1))
        title = _strip_html(html.unescape(match.group(2))).strip()
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc == "duckduckgo.com":
            params = urllib.parse.parse_qs(parsed.query)
            href = params.get("uddg", [href])[0]
            parsed = urllib.parse.urlparse(href)
        if allowed and not any(parsed.netloc.lower().endswith(domain) for domain in allowed):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break
    return {"tool": call.name, "args": call.args, "ok": True, "results": results, "summary": f"{len(results)} web result(s); cite URLs and keep web evidence separate from run evidence"}


def _list_mcp_servers_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    servers = load_mcp_registry()
    return {"tool": call.name, "args": call.args, "ok": True, "servers": servers, "summary": f"{len(servers)} configured MCP server(s)"}


def _list_mcp_tools_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _mcp_failure(call, f"MCP server not found: {call.args['server']}")
    timeout = int(call.args.get("timeout_seconds") or 5)
    refresh = bool(call.args.get("refresh"))
    ttl = int(call.args.get("cache_ttl_seconds") or mcp_cache.DEFAULT_TTL_SECONDS)
    if not refresh:
        cached = mcp_cache.read_cached_tools(server, ttl_seconds=ttl)
        if cached is not None:
            mapped = [_map_mcp_tool_schema(str(server["name"]), tool) for tool in cached if isinstance(tool, dict)]
            return {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "tools": cached,
                "mapped_tools": mapped,
                "cache": "hit",
                "summary": f"{len(cached)} cached MCP tool(s) on {server['name']}; {len(mapped)} schema(s) mapped for planner inspection",
            }
    try:
        tools = mcp_client.list_tools(str(server["command"]), [str(arg) for arg in server.get("args", [])], timeout=timeout)
    except Exception as exc:
        return _mcp_failure(call, f"MCP tools/list failed: {exc}")
    cache_path = mcp_cache.write_cached_tools(server, tools)
    mapped = [_map_mcp_tool_schema(str(server["name"]), tool) for tool in tools if isinstance(tool, dict)]
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "tools": tools,
        "mapped_tools": mapped,
        "cache": "refresh" if refresh else "miss",
        "cache_path": str(cache_path),
        "summary": f"{len(tools)} MCP tool(s) on {server['name']}; {len(mapped)} schema(s) mapped for planner inspection; cached schema",
    }


def _call_mcp_tool_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _mcp_failure(call, f"MCP server not found: {call.args['server']}")
    arguments = call.args.get("arguments") if isinstance(call.args.get("arguments"), dict) else {}
    try:
        result = mcp_client.call_tool(str(server["command"]), [str(arg) for arg in server.get("args", [])], str(call.args["tool"]), arguments)
    except Exception as exc:
        return _mcp_failure(call, f"MCP tools/call failed: {exc}", server=str(server["name"]))
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"called MCP tool {server['name']}.{call.args['tool']}"}


def _mcp_failure(call: ToolCall, summary: str, *, server: str | None = None) -> dict[str, Any]:
    result = _failure(call, summary)
    result["recovery"] = "Use list_mcp_servers/list_mcp_tools to refresh available MCP tools, then retry with corrected server/tool/arguments or fall back to the CLI wrapper."
    result["fallback_tools"] = _mcp_fallback_tools(server or str(call.args.get("server") or ""))
    return result


def _map_mcp_tool_schema(server_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "tool")
    description = str(tool.get("description") or f"MCP tool exposed by {server_name}.")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
    parameters = _normalize_json_schema(schema)
    return {
        "server": server_name,
        "mcp_tool": name,
        "planner_tool": "call_mcp_tool",
        "description": description,
        "arguments": {"server": server_name, "tool": name, "arguments_schema": parameters},
    }


def _normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", True)
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _mcp_fallback_tools(server_name: str) -> list[str]:
    mapping = {
        "swmm-builder": ["build_inp"],
        "swmm-climate": ["format_rainfall"],
        "swmm-network": ["network_qa", "network_to_inp"],
        "swmm-plot": ["plot_run"],
        "swmm-runner": ["run_swmm_inp"],
    }
    return mapping.get(server_name, ["list_mcp_servers", "list_mcp_tools"])


def _capabilities_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    names = sorted(_build_tools())
    return {"tool": call.name, "args": call.args, "ok": True, "capabilities": capability_summary(names), "summary": "runtime capabilities returned"}


def _workflow_user_prompt(mode: str, missing: list[str]) -> str:
    if not missing:
        return "Inputs are sufficient for the selected workflow mode. Continue with the recommended tools."
    if mode == "needs_user_inputs":
        return "Please provide a SWMM INP path, or the complete full-build input set: network_json, subcatchments_csv, rainfall_input, landuse_input, and soil_input."
    return "Please provide: " + ", ".join(missing)


def _active_run_dir_from_global_state() -> str | None:
    path = runtime_state_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("active_run_dir")
    return str(value) if value else None


def _run_cli_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, "-m", "agentic_swmm.cli", *cli_args], cwd=repo_root())


def _run_script_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, *cli_args], cwd=repo_root())


def _run_process_tool(call: ToolCall, session_dir: Path, command: list[str], *, cwd: Path, timeout: int = 120) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=runtime_env(), timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(command, 124, stdout=exc.stdout or "", stderr=exc.stderr or f"command timed out after {timeout}s")
        timed_out = True
    finished = datetime.now(timezone.utc)
    stdout = _process_text(proc.stdout)
    stderr = _process_text(proc.stderr)
    safe_name = _safe_name(call.name)
    stdout_path = session_dir / "tool_results" / f"{safe_name}.stdout.txt"
    stderr_path = session_dir / "tool_results" / f"{safe_name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {"tool": call.name, "args": call.args, "command": command, "ok": proc.returncode == 0, "return_code": proc.returncode, "timed_out": timed_out, "started_at_utc": started.isoformat(timespec="seconds"), "finished_at_utc": finished.isoformat(timespec="seconds"), "stdout_file": str(stdout_path), "stderr_file": str(stderr_path), "stdout_tail": _tail(stdout), "stderr_tail": _tail(stderr), "summary": _summarize_cli_result(call.name, stdout, proc.returncode)}


def _run_tests_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    paths = call.args.get("paths")
    test_paths = [str(path) for path in paths] if isinstance(paths, list) and paths else ["tests"]
    for path in test_paths:
        resolved = _repo_path(path)
        if resolved is None:
            return _failure(call, f"test path must be inside repository: {path}")
    timeout = int(call.args.get("timeout_seconds") or 120)
    if importlib.util.find_spec("pytest") is None and len(test_paths) == 1 and test_paths[0].endswith(".py"):
        return _run_process_tool(call, session_dir, [sys.executable, test_paths[0]], cwd=repo_root(), timeout=timeout)
    return _run_process_tool(call, session_dir, [sys.executable, "-m", "pytest", *test_paths], cwd=repo_root(), timeout=timeout)


def _run_allowed_command_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = call.args.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        return _failure(call, "command must be a non-empty string array")
    if not _command_allowed(command):
        return _failure(call, "command is not allowlisted")
    timeout = int(call.args.get("timeout_seconds") or 120)
    return _run_process_tool(call, session_dir, command, cwd=repo_root(), timeout=timeout)


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if return_code != 0:
        return f"{tool} failed"
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        if "run_dir" in parsed:
            return f"run_dir={parsed['run_dir']}"
        if "experiment_note" in parsed:
            return f"audit_note={parsed['experiment_note']}"
        if "ok" in parsed and "issue_count" in parsed:
            return f"ok={parsed['ok']} issue_count={parsed['issue_count']}"
        if "outputs" in parsed:
            return "outputs=" + json.dumps(parsed["outputs"], sort_keys=True)[:500]
    stripped = stdout.strip().splitlines()
    return stripped[-1] if stripped else "completed"


def _repo_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


def _repo_output_path(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _node_suggestions(inp_path: str | None, limit: int = 8) -> list[str]:
    if not inp_path:
        return []
    candidate = _resolve_existing_inp(inp_path)
    if candidate is None:
        return []
    sections: dict[str, list[str]] = {"[OUTFALLS]": [], "[JUNCTIONS]": []}
    section: str | None = None
    for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.upper()
            continue
        if section in {"[OUTFALLS]", "[JUNCTIONS]"}:
            name = stripped.split()[0]
            if name not in sections[section]:
                sections[section].append(name)
    suggestions = [*sections["[OUTFALLS]"], *sections["[JUNCTIONS]"]]
    deduped = list(dict.fromkeys(suggestions))
    return deduped[:limit]


def _plot_selection_options_for_inp(inp_path: str | None) -> dict[str, Any]:
    if not inp_path:
        return {"rainfall_options": [], "node_attribute_options": _default_node_attribute_options()}
    inp = _resolve_existing_inp(inp_path)
    if inp is None:
        return {"rainfall_options": [], "node_attribute_options": _default_node_attribute_options()}
    return {
        "rainfall_options": rainfall_timeseries_options(inp),
        "node_attribute_options": _default_node_attribute_options(),
    }


def _node_attribute_options(out_file: Path | None, node_options: list[str]) -> list[dict[str, Any]]:
    if out_file is None or not out_file.exists():
        return _default_node_attribute_options()
    try:
        from swmmtoolbox import catalog

        rows = catalog(str(out_file), "node")
    except Exception:
        return _default_node_attribute_options()
    attrs: list[str] = []
    for row in rows:
        if len(row) < 3 or row[0] != "node":
            continue
        node, attr = str(row[1]), str(row[2])
        if node_options and node not in node_options:
            continue
        if attr not in attrs:
            attrs.append(attr)
    if not attrs:
        return _default_node_attribute_options()
    preferred = [attr for attr in NODE_ATTRIBUTE_CHOICES if attr in attrs]
    remainder = [attr for attr in attrs if attr not in preferred]
    return [{"name": attr, "label": NODE_ATTRIBUTE_LABELS.get(attr, attr.replace("_", " "))} for attr in [*preferred, *remainder]]


def _default_node_attribute_options() -> list[dict[str, str]]:
    return [{"name": attr, "label": NODE_ATTRIBUTE_LABELS.get(attr, attr.replace("_", " "))} for attr in NODE_ATTRIBUTE_CHOICES]


def _resolve_existing_inp(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None and path.exists() and path.is_file() and path.suffix.lower() == ".inp":
        return path
    external = Path(value).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return None
    if external.exists() and external.is_file() and external.suffix.lower() == ".inp":
        return external
    return _find_repo_inp(value)


def _process_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            path = line[6:].strip()
            if path != "/dev/null" and path not in paths:
                paths.append(path)
        elif line.startswith("diff --git "):
            parts = line.split()
            for part in parts[2:4]:
                if part.startswith(("a/", "b/")):
                    path = part[2:]
                    if path not in paths:
                        paths.append(path)
    return paths


def _command_allowed(command: list[str]) -> bool:
    exe = Path(command[0]).name.lower()
    if exe in {"pytest", "pytest.exe"}:
        return True
    if exe in {"python", "python.exe"} or command[0] == sys.executable:
        return len(command) >= 3 and command[1] == "-m" and command[2] in {"pytest", "agentic_swmm.cli"}
    if exe in {"node", "node.exe"}:
        return len(command) >= 2 and _repo_path(command[1]) is not None and Path(command[1]).suffix == ".mjs" and str(Path(command[1])).replace("\\", "/").startswith("scripts/")
    if exe in {"swmm5", "swmm5.exe", "swmm5.cmd"}:
        return True
    return False


def _required_repo_file(call: ToolCall, key: str, *, suffix: str | None = None) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required file argument: {key}")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    if suffix and path.suffix.lower() != suffix:
        return _failure(call, f"{key} must end with {suffix}")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    return path


def _resolve_inp_for_run(call: ToolCall) -> Path | dict[str, Any]:
    raw = str(call.args.get("inp_path", "")).strip()
    if not raw:
        return _failure(call, "missing required file argument: inp_path")
    repo_file = _required_repo_file(call, "inp_path", suffix=".inp")
    if not isinstance(repo_file, dict):
        return repo_file
    resolved = _find_repo_inp(raw)
    if resolved is not None:
        return resolved
    external = Path(raw).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return _failure(call, f"inp_path could not be resolved: {raw}")
    if external.suffix.lower() != ".inp":
        return _failure(call, "inp_path must end with .inp")
    if not external.exists() or not external.is_file():
        return _failure(call, f"external INP file not found: {external}")
    return external


def _find_repo_inp(value: str) -> Path | None:
    if not value or Path(value).is_absolute() or "/" in value:
        return None
    root = repo_root() / "examples"
    if not root.exists():
        return None
    matches = sorted(path for path in root.rglob(value) if path.is_file() and path.suffix.lower() == ".inp")
    return matches[0] if matches else None


def _required_repo_dir(call: ToolCall, key: str) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required directory argument: {key}")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    if not path.exists() or not path.is_dir():
        return _failure(call, f"directory not found: {path}")
    return path


def _optional_repo_output_dir(call: ToolCall, key: str) -> Path | dict[str, Any] | None:
    value = call.args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"{key} must be a non-empty string")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mcp_server(name: str) -> dict[str, Any] | None:
    for server in load_mcp_registry():
        if str(server.get("name")) == name:
            return server
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    return {"tool": call.name, "args": call.args, "ok": False, "summary": summary}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tail(text: str, max_chars: int = 2000) -> str:
    return text.strip()[-max_chars:]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"
