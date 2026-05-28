"""Central :class:`AgentToolRegistry` and :class:`ToolSpec` plumbing.

PR #128 split the historic god-module ``tool_registry.py`` into the
``tool_handlers/`` package so each tool family (swmm_runner, swmm_plot,
swmm_builder, etc.) lives in its own deep module. The handler families
listed below are intentionally retained here and are NOT scheduled for
extraction under that plan:

* ``_capabilities_tool`` — surfaces the registry's own ToolSpec listing,
  so it logically belongs in the registry that owns the data.
* ``_list_mcp_servers_tool``, ``_list_mcp_tools_tool``,
  ``_call_mcp_tool_tool``, ``_mcp_failure`` — the MCP bridge. These
  proxy through to ``mcp_client`` / ``mcp_pool`` and only exist to wrap
  external MCP calls into the local ToolCall shape; splitting them out
  would gain nothing.
* ``_node_suggestions``, ``_plot_selection_options_for_inp``,
  ``_run_tests_tool``, ``_run_allowed_command_tool`` — registry-internal
  helpers consumed by the deep-module args mappers (Group A's
  ``plot_run`` / ``run_swmm_inp``); moving them would create an upward
  dependency from a deep module back into the registry.

If a future refactor wants to revisit this, this docstring is the
record of the intentional decision — it isn't an oversight from the
PR #128 split.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent import mcp_cache, mcp_client
from agentic_swmm.agent.mcp_client import McpClientError
from agentic_swmm.agent.mcp_pool import ensure_session_pool
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _repo_output_path,
    _repo_path,
    _run_process_tool,
    _safe_name,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.commands.plot import NODE_ATTRIBUTE_CHOICES, NODE_ATTRIBUTE_LABELS, rainfall_timeseries_options
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.runtime.registry import load_mcp_registry
from agentic_swmm.utils.paths import repo_root


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
            # CONCURRENCY-OWNER: PRD-GF-L5
            # ``request_gap_judgement`` returns ``resume_mode=llm_replan``
            # plus ``gap_kind`` so the planner's replan-injection branch
            # can recognise the L5 result and pull the recorded decision
            # into the next LLM turn as a user_clarification message.
            "resume_mode",
            "gap_kind",
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
        # CONCURRENCY-OWNER: PRD-GF-L5
        # L5 subjective judgement entry point. The LLM invokes this
        # tool explicitly when it identifies a hydrological choice that
        # has no single right answer (pour point, storm event, metric
        # weighting, …). The handler:
        #   1. asks the LLM to enumerate N candidates with each one's
        #      tradeoff cited (``llm_enumerator``),
        #   2. shows the per-gap pause UI (``ui_per_gap``),
        #   3. records an L5 ``GapDecision`` via the gap-fill recorder.
        # ``supports_gap_fill=False`` because L5 does *not* travel the
        # L1/L3 ``gap_signal`` interception path — the agent calls the
        # tool directly. ``is_read_only=False`` because judgement must
        # never be auto-approved by the QUICK profile.
        ToolSpec(
            "request_gap_judgement",
            (
                "Request a subjective hydrological judgement from the human "
                "expert with enumerated candidates.\n"
                "USE WHEN: a hydrological choice has no single right answer "
                "(pour point ambiguity, storm event selection from a "
                "calibration window, metric weighting, continuity tolerance "
                "deviation). The LLM enumerator will list candidates with "
                "each one's tradeoff cited; the modeller picks one with an "
                "optional free-form note; the planner re-plans on the next "
                "turn with the decision as a user_clarification message.\n"
                "DO NOT USE WHEN: a missing path or parameter value can be "
                "proposed deterministically (those flow through the L1/L3 "
                "gap-fill path automatically). For free-form pauses without "
                "structured candidates, use request_expert_review."
            ),
            _object(
                {
                    "gap_kind": {
                        "type": "string",
                        "enum": [
                            "pour_point",
                            "storm_event_selection",
                            "metric_weighting",
                            "continuity_tolerance",
                        ],
                    },
                    "context": {"type": "object"},
                    "evidence_ref": {"type": "string"},
                },
                ["gap_kind", "context", "evidence_ref"],
            ),
            _request_gap_judgement_tool,
            # is_read_only=False — judgement must never be auto-approved.
            is_read_only=False,
            # supports_gap_fill=False — L5 is a separate mechanism from
            # the L1/L3 ``gap_signal`` interception path. The agent
            # invokes this tool explicitly; the runtime does not wrap
            # it with the GF-CORE state machine.
            supports_gap_fill=False,
        ),
        ToolSpec("run_swmm_inp", "Run a repository or imported external .inp file through the constrained swmm-runner CLI wrapper.", _object({"inp_path": {"type": "string"}, "run_id": {"type": "string"}, "run_dir": {"type": "string"}, "node": {"type": "string"}}, ["inp_path"]), _run_swmm_inp_tool),
        ToolSpec(
            "synth_swmm_from_bbox",
            (
                "Synthesise a SWMM .inp file from a WGS84 bounding box using the "
                "swmm-anywhere skill, which wraps ImperialCollegeLondon/SWMManywhere "
                "(BSD-3-Clause).\n"
                "USE WHEN: the user wants to build a SWMM model from a geographic "
                "region and has not supplied a shapefile or pre-built network — "
                "i.e. there is no SHP / GeoJSON / network_json input, just a bbox "
                "or a place name with coordinates.\n"
                "DO NOT USE WHEN: the user supplied a SHP / network_json / "
                "existing .inp (those flow through build_inp, network_to_inp, "
                "run_swmm_inp instead).\n"
                "Requires the optional [anywhere] extra; the tool returns a "
                "stage-tagged hint if the extra is missing."
            ),
            _object(
                {
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": "WGS84 bounding box [min_lon, min_lat, max_lon, max_lat].",
                    },
                    "run_dir": {"type": "string"},
                    "project_name": {"type": "string"},
                    "refresh_raw": {"type": "boolean"},
                    "upstream_defaults": {
                        "type": "boolean",
                        "description": "When true, skip aiswmm's tuned outfall_derivation overrides and use SWMManywhere's upstream defaults.",
                    },
                    "rain_file": {
                        "type": "string",
                        "description": "Optional absolute path to a SWMM-format DAT rainfall file to replace the bundled storm.dat.",
                    },
                },
                ["bbox"],
            ),
            _synth_swmm_from_bbox_tool,
        ),
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
        # LLM-driven dispatch refactor: ``select_workflow_mode`` removed.
        # Frontier LLMs read each tool's description / SKILL.md and
        # pick the right tool directly; the hardcoded mode enum was a
        # GPT-4-era guardrail that re-introduced keyword-matching
        # brittleness on top of the LLM's own classifier.
        ToolSpec("summarize_memory", "Summarize audited runs into the modeling-memory directory.", _object({"runs_dir": {"type": "string"}, "out_dir": {"type": "string"}}, ["runs_dir"]), _summarize_memory_tool),
        ToolSpec(
            "retrieve_memory",
            "Retrieve relevant audited-run memory cards for a query using the swmm-rag-memory skill's hybrid keyword/embedding retriever. Returns source-cited matches that the planner can synthesize into a grounded answer.",
            _object(
                {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                    "retriever": {"type": "string", "enum": ["keyword", "hybrid"]},
                    "project": {"type": "string"},
                },
                ["query"],
            ),
            _retrieve_memory_tool,
            is_read_only=True,
        ),
        ToolSpec("web_fetch_url", "Fetch and summarize a web page. Web evidence is not SWMM run evidence.", _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}), _web_fetch_url_tool, is_read_only=True),
        ToolSpec("web_search", "Run a lightweight web search and return cited result URLs. Web evidence is not SWMM run evidence.", _object({"query": {"type": "string"}, "allowed_domains": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}}), _web_search_tool, is_read_only=True),
    ]
    return {spec.name: spec for spec in specs}


# PRD #128 Phase 2 Group C: ``_doctor_tool`` and ``_retrieve_memory_tool``
# moved to ``tool_handlers/introspection.py``. Re-exported here so import
# paths stay stable.
from agentic_swmm.agent.tool_handlers.introspection import (  # noqa: E402,F401
    _doctor_tool,
    _retrieve_memory_tool,
)


# PRD #128 Phase 2 Group C: HITL / L5 gap-fill governance handlers moved
# to ``tool_handlers/gap_fill.py``. Re-exported here so import paths stay
# stable — ``_is_tty_for_l5`` is monkeypatched by the L5 headless-block
# tests at ``agentic_swmm.agent.tool_registry._is_tty_for_l5`` and that
# path must keep resolving.
from agentic_swmm.agent.tool_handlers.gap_fill import (  # noqa: E402,F401
    _build_default_llm_provider,
    _is_tty_for_l5,
    _request_expert_review_tool,
    _request_gap_judgement_tool,
    _restitch_l5_fields_in_ledger,
)


# PRD #128 Phase 2 Group C: runtime file/repo/skill ops moved to
# ``tool_handlers/runtime_ops.py`` (together with the
# ``_patch_paths`` / ``_normalize_search_glob`` helpers). Re-exported
# here so import paths stay stable.
from agentic_swmm.agent.tool_handlers.runtime_ops import (  # noqa: E402,F401
    _apply_patch_tool,
    _git_diff_tool,
    _list_dir_tool,
    _list_skills_tool,
    _normalize_search_glob,
    _patch_paths,
    _read_file_tool,
    _read_skill_tool,
    _search_files_tool,
)



# PRD #128: ``_demo_acceptance_tool`` moved to ``tool_handlers/demo.py``.
# Re-exported here so import paths stay stable.
from agentic_swmm.agent.tool_handlers.demo import (  # noqa: E402,F401
    _demo_acceptance_tool,
)


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


# PRD #128 Phase 2 Group B: ``_audit_run_args`` / ``_audit_run_tool``
# moved to ``tool_handlers/swmm_audit.py``. Re-exported here so import
# paths stay stable for ``_build_tools`` and downstream code.
from agentic_swmm.agent.tool_handlers.swmm_audit import (  # noqa: E402,F401
    _audit_run_args,
    _audit_run_tool,
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


# PRD #128 Phase 2 Group C: ``_retrieve_memory_tool`` (the swmm-rag-memory
# retriever shim from Issue #124 Part A) moved to
# ``tool_handlers/introspection.py`` along with the ``_RAG_SKILL_DIR_RELATIVE``
# private constant. Re-exported above via the introspection module.


# -- Memory recall tools (PRD M1, M6, M7.1) -----------------------------------
#
# PRD #128: the four memory-family handlers (`_recall_memory_tool`,
# `_recall_memory_search_tool`, `_recall_session_history_tool`,
# `_record_fact_tool`) moved to ``tool_handlers/swmm_memory.py`` along
# with their token-budget / lessons-path helpers. Re-exported here so
# import paths stay stable for ``_build_tools`` and downstream code.

from agentic_swmm.agent.tool_handlers.swmm_memory import (  # noqa: E402,F401
    _recall_memory_search_tool,
    _recall_memory_tool,
    _recall_session_history_tool,
    _record_fact_tool,
)


# PRD #128 Phase 2 Group C: ``_read_file_tool``, ``_list_skills_tool``,
# ``_read_skill_tool`` moved to ``tool_handlers/runtime_ops.py`` (read-only
# file / skill introspection family). Re-exported above via runtime_ops.


# PRD #128 Phase 2 Group A: ``_run_swmm_inp_args`` /
# ``_run_swmm_inp_tool`` moved to ``tool_handlers/swmm_runner.py``;
# ``_inspect_plot_options_tool`` moved to
# ``tool_handlers/swmm_plot.py``. Re-exported at the bottom of this
# file (after all helpers are defined) so the cycling import chain
# resolves cleanly.


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


# LLM-driven dispatch refactor: the workflow-mode adapter registry
# (``agentic_swmm.agent.workflow_modes``) and the
# ``select_workflow_mode`` handler module have been deleted. The LLM
# now reads each tool's description / SKILL.md and picks tools
# directly — see ``.claude/prds/PRD_llm_driven_dispatch.md`` for the
# decision record. ``compute_intent_signals`` survives because the
# warm-intro classifier (``intent_classifier``) still consumes its
# legacy dict shape; the function is now a thin adapter only.


def compute_intent_signals(goal: str) -> dict[str, bool]:
    """Compute the keyword-derived ``wants_*`` flags for a goal.

    PRD #121 made ``agentic_swmm.agent.intent_classifier`` the single
    source of truth for keyword-driven intent extraction. This function
    is the legacy-shaped adapter that any remaining keyword-driven
    surface (warm-intro gates, tests) consumes; new callers should use
    ``intent_classifier.classify_intent`` directly.
    """

    # Late import keeps the tool_registry → intent_classifier edge one
    # way; intent_classifier must stay dependency-free of tool_registry.
    from agentic_swmm.agent.intent_classifier import classify_intent

    return classify_intent(goal).as_dict()


# PRD #128 Phase 2 Group A: ``_plot_run_args`` / ``_plot_run_tool``
# moved to ``tool_handlers/swmm_plot.py``. Re-exported at the bottom
# of this file (after all helpers are defined).


# PRD #128 Phase 2 Group B: ``_network_qa_args`` / ``_network_qa_tool``
# and ``_network_to_inp_args`` / ``_network_to_inp_tool`` moved to
# ``tool_handlers/swmm_network.py``. Re-exported here so import paths
# stay stable for ``_build_tools`` and downstream code.
from agentic_swmm.agent.tool_handlers.swmm_network import (  # noqa: E402,F401
    _network_qa_args,
    _network_qa_tool,
    _network_to_inp_args,
    _network_to_inp_tool,
)


# PRD #128 Phase 2 Group B: ``_format_rainfall_args`` / ``_format_rainfall_tool``
# moved to ``tool_handlers/swmm_climate.py``. Re-exported here so import
# paths stay stable for ``_build_tools`` and downstream code.
from agentic_swmm.agent.tool_handlers.swmm_climate import (  # noqa: E402,F401
    _format_rainfall_args,
    _format_rainfall_tool,
)


# PRD #128 Phase 2 Group A: ``_build_inp_args`` / ``_build_inp_tool``
# moved to ``tool_handlers/swmm_builder.py``. Re-exported at the
# bottom of this file (after all helpers are defined).


# PRD #128 Phase 2 Group C: ``_list_dir_tool``, ``_search_files_tool``,
# ``_normalize_search_glob``, ``_git_diff_tool`` moved to
# ``tool_handlers/runtime_ops.py``. Re-exported above via runtime_ops.


# PRD #128: ``_web_fetch_url_tool`` and ``_web_search_tool`` moved to
# ``tool_handlers/web.py``. Re-exported here so import paths stay stable.
from agentic_swmm.agent.tool_handlers.web import (  # noqa: E402,F401
    _web_fetch_url_tool,
    _web_search_tool,
)


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


# PRD #128 Phase 2 Group C: ``_workflow_user_prompt`` and
# ``_active_run_dir_from_global_state`` moved to
# ``tool_handlers/workflow_mode.py``. Re-exported above.


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


# PRD #128 Phase 2 Group C: ``_patch_paths`` moved to
# ``tool_handlers/runtime_ops.py`` alongside ``_apply_patch_tool``
# (its sole caller). Re-exported above via runtime_ops.


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


# PRD #128 Phase 2 Group A: family-module re-exports.
#
# These imports sit at the very end of this file so every helper the
# family modules pull back in (``_make_mcp_routed_handler``,
# ``_resolve_inp_for_run``, ``_node_suggestions``,
# ``_node_attribute_options``, ``_resolve_existing_inp``,
# ``_required_repo_file``, ``_required_repo_dir``,
# ``_optional_repo_output_dir``) is already bound on the partial
# ``tool_registry`` module by the time the family submodules execute
# their ``from agentic_swmm.agent.tool_registry import ...`` statements.
# Re-exporting the handler symbols here keeps ``_build_tools()`` and
# any existing ``from agentic_swmm.agent.tool_registry import _*_args``
# call sites working byte-for-byte after the move.
from agentic_swmm.agent.tool_handlers.swmm_runner import (  # noqa: E402,F401
    _run_swmm_inp_args,
    _run_swmm_inp_tool,
)
from agentic_swmm.agent.tool_handlers.swmm_plot import (  # noqa: E402,F401
    _inspect_plot_options_tool,
    _plot_run_args,
    _plot_run_tool,
)
from agentic_swmm.agent.tool_handlers.swmm_builder import (  # noqa: E402,F401
    _build_inp_args,
    _build_inp_tool,
)
# LLM-driven dispatch refactor: ``swmm-anywhere`` handler is in-process
# (not MCP-routed), so it re-exports cleanly here without needing the
# late-import dance the MCP-routed families use.
from agentic_swmm.agent.tool_handlers.swmm_anywhere import (  # noqa: E402,F401
    _synth_swmm_from_bbox_tool,
)


