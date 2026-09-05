"""Central :class:`AgentToolRegistry` and :class:`ToolSpec` plumbing.

PR #128 split the historic god-module ``tool_registry.py`` into the
``tool_handlers/`` package so each tool family (swmm_runner, swmm_plot,
swmm_builder, etc.) lives in its own deep module. The handler families
listed below are intentionally retained here and are NOT scheduled for
extraction under that plan:

* ``_capabilities_tool`` — surfaces the registry's own ToolSpec listing,
  so it logically belongs in the registry that owns the data.
* ``_list_mcp_servers_tool``, ``_list_mcp_tools_tool``,
  ``_call_mcp_tool_tool`` — the MCP bridge. These proxy through to
  ``mcp_client`` / ``mcp_pool`` and only exist to wrap external MCP
  calls into the local ToolCall shape; splitting them out would gain
  nothing.
* ``_run_tests_tool``, ``_run_allowed_command_tool`` — thin registered
  handlers over the shared allowlist helpers.

The pure leaf helpers that used to live here (schema building, the
repo-sandboxed path/INP resolvers, node/plot option derivation, MCP
schema mapping, the command allowlist) moved to
``tool_handlers/_shared.py`` in issue #358 PR A and are re-imported at
the top of this file, which is what finally lets family modules import
them without riding the registry's end-of-file cycle.

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
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _make_mcp_routed_handler,
    _wrap_mcp_result,
    _repo_output_path,
    _repo_path,
    _run_process_tool,
    _safe_name,
    # Leaf helpers extracted in issue #358 PR A. Re-imported here so every
    # historical ``from agentic_swmm.agent.tool_registry import X`` site
    # (family modules' lazy imports, tests, and the
    # ``monkeypatch.setattr(tool_registry, "_resolve_inp_for_run", ...)``
    # seam) keeps working byte-for-byte, and so the names are bound on the
    # partial module before the end-of-file family imports execute.
    _PYTEST_BANNED_FLAGS,
    _command_allowed,
    _default_node_attribute_options,
    _find_repo_inp,
    _map_mcp_tool_schema,
    _mcp_failure,
    _mcp_fallback_tools,
    _mcp_server,
    _node_attribute_options,
    _node_script_ok,
    _node_suggestions,
    _normalize_json_schema,
    _object,
    _pytest_args_ok,
    _required_repo_file,
    _resolve_existing_inp,
    _resolve_inp_for_run,
)
from agentic_swmm.agent.types import ToolCall, ToolSpec
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.runtime.registry import load_mcp_registry
from agentic_swmm.utils.paths import repo_root


# ``ToolSpec`` moved to ``agentic_swmm.agent.types`` (issue #358 PR B) so
# family modules declare their own specs without importing this registry;
# re-imported above so every historical
# ``from agentic_swmm.agent.tool_registry import ToolSpec`` site keeps
# working. ``execute`` still routes through the gap-fill wrapper here.


def _control_flow_exception_types() -> tuple[type[BaseException], ...]:
    """Exceptions the tool-execution boundary must re-raise, not swallow.

    These are deliberate control-flow signals (human-in-the-loop escalation and
    gap-fill resolution), not tool failures. Imported lazily to avoid an
    import cycle with the gap-fill and policy modules.
    """
    from agentic_swmm.agent.memory_informed_policy import MemoryHITLRequired
    from agentic_swmm.gap_fill.proposer import GapFillRegistryOnlyMiss
    from agentic_swmm.gap_fill.ui import GapFillNonInteractive, GapFillRejected

    return (MemoryHITLRequired, GapFillRejected, GapFillNonInteractive, GapFillRegistryOnlyMiss)


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools = _build_tools()

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def sorted_names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, names: "set[str] | None" = None) -> list[dict[str, Any]]:
        """Tool schemas for the model, all of them or the named subset.

        ``names`` is the goal-scoped subset the planner computes when
        the goal-scoped subset, on by default, AISWMM_TOOL_SUBSET=0 disables (live finding F-44, 2026-09-02: all 57
        schemas, 64k characters, are the bulk of every LLM call's input).
        Unknown names are ignored so a stale subset cannot raise.
        """
        selected = sorted(self._tools) if names is None else sorted(n for n in self._tools if n in names)
        return [self._tools[name].schema() for name in selected]

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
        try:
            if getattr(spec, "supports_gap_fill", False):
                from agentic_swmm.agent.runtime_loop import invoke_tool_with_gap_fill

                return invoke_tool_with_gap_fill(
                    spec, call, session_dir, lambda c, sd: spec.handler(c, sd)
                )
            return spec.handler(call, session_dir)
        except Exception as exc:  # noqa: BLE001
            # HITL/gap-fill signals are deliberate control flow — let them
            # propagate. Everything else (e.g. a missing required argument the
            # model omitted, review P2-3) must become a normal failed tool
            # result the planner can see, not an uncaught exception that kills
            # the whole session and bypasses the fail-soft machinery.
            if isinstance(exc, _control_flow_exception_types()):
                raise
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": f"{call.name} raised {type(exc).__name__}: {exc}",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

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

    def mcp_routing(self, name: str) -> dict[str, str] | None:
        """Return ``{"server", "tool"}`` for MCP-routed tools, else ``None``.

        Handlers built by ``_make_mcp_routed_handler`` carry their
        routing metadata; this is the public query surface so tests and
        diagnostics can assert "tool X routes through MCP server Y"
        without parsing this module's source or reading closure
        internals. In-process handlers (and unknown names) return
        ``None``.
        """
        spec = self._tools.get(name)
        if spec is None:
            return None
        return getattr(spec.handler, "_mcp_routing", None)

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
            # Structured path/file-resolution remediation
            # (error_remediation.file_resolution_error): the planner needs
            # the actionable hint + cause to self-correct a bad path.
            "hint",
            "cause",
            # Live finding F-31 (2026-09-02): read_rpt_summary returned its
            # ranked rows under top-level ``rows`` / ``section`` / ``sort_by``
            # keys that this allowlist dropped, so the planner received only
            # "section=... total=307 shown=3" and went looking for the rows in
            # the trace file for 30 steps, session after session. Data keys
            # a typed tool answers with are part of the answer.
            "rows",
            "section",
            "total_rows",
            "shown",
            "sort_by",
            "skipped_malformed_rows",
            "wq_present",
            "answer_ready",
            "note",
            # run_climate_scenarios answers under these; the model saw only
            # "3/3 scenarios ran" (same defect class, found in the F-31 sweep).
            "run_dir",
            "node",
            "summary_json",
            "summary_md",
            "scenarios",
            # propagate_parameter_ranges (user decision 2026-09-02, F-55)
            "baseline_peak",
            "flow_units",
            "stats",
            "samples",
            "ranges",
            "evidence_boundary",
            # list_canada_cities (F-149): the list is the answer.
            "cities",
            "count",
            "service_url",
        }
        return {key: value for key, value in result.items() if key in allowed_keys}


# ``_object`` and the other leaf schema/path/allowlist helpers moved to
# ``tool_handlers/_shared.py`` (issue #358 PR A); they are re-imported at
# the top of this file.


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


# ``_CALIBRATION_COMMON_REQUIRED`` moved to
# ``tool_handlers/swmm_calibration.py`` with the family's specs
# (issue #358 C3).


def _registry_native_tools() -> list[ToolSpec]:
    """The tools that genuinely belong to the registry itself.

    Everything else self-registers through the family seam
    (``_FAMILY_SPEC_MODULES``). These eight stay because their handlers
    reference registry/runtime state by design: ``capabilities`` and
    ``select_skill`` introspect the registry, the MCP bridge trio proxies
    the local MCP registry, ``run_tests`` / ``run_allowed_command`` wrap
    the shared allowlist, and ``summarize_memory``'s args mapper is a
    registry-resident seam with external importers (issue #358 C5).
    """
    return [
        ToolSpec("capabilities", "Describe what this runtime can and cannot access.", _object({}), _capabilities_tool, is_read_only=True),
        ToolSpec("list_mcp_servers", "List configured local MCP servers.", _object({}), _list_mcp_servers_tool, is_read_only=True),
        ToolSpec("list_mcp_tools", "List tools exposed by one configured MCP server (names + one-line descriptions by default; pass full=true for complete schemas; select_skill already returns the chosen skill's full contracts). timeout_seconds defaults to 10; a Node server needs several seconds to start on a busy machine, so do not ask for less.", _object({"server": {"type": "string"}, "timeout_seconds": {"type": "integer"}, "refresh": {"type": "boolean"}, "cache_ttl_seconds": {"type": "integer"}, "full": {"type": "boolean"}}, ["server"]), _list_mcp_tools_tool, is_read_only=True),
        ToolSpec("call_mcp_tool", "Call a tool exposed by a configured local MCP server.", _object({"server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}}, ["server", "tool"]), _call_mcp_tool_tool),
        ToolSpec("run_allowed_command", f"Run ONE allowlisted local command. Allowed forms: {ALLOWED_COMMAND_FORMS}. {_NOT_A_SHELL_HINT}", _object({"command": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}, ["command"]), _run_allowed_command_tool),
        ToolSpec("run_tests", "Run pytest on selected repository test paths.", _object({"paths": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer"}}), _run_tests_tool),
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
        ToolSpec("summarize_memory", "Summarize audited runs into the modeling-memory directory.", _object({"runs_dir": {"type": "string"}, "out_dir": {"type": "string"}, "obsidian_dir": {"type": "string", "description": "Optional path to an Obsidian vault directory; when present, the skill writes a Markdown summary there in addition to the standard output."}}, ["runs_dir"]), _summarize_memory_tool),
    ]


# The six swmm-calibration ToolSpecs moved to
# swmm_calibration.tool_specs() together with
# _CALIBRATION_COMMON_REQUIRED (issue #358 C3).


# Web and swmm-uncertainty specs moved to web.tool_specs() and
# swmm_uncertainty.tool_specs() (issue #358 C4).


# Family self-registration seam (issue #358 PR B). A tool_handlers
# family module listed here defines ``tool_specs() -> list[ToolSpec]``
# next to its handlers, importing ToolSpec from ``agent.types`` and the
# schema/path helpers from ``tool_handlers/_shared`` — never from this
# registry. Adding a family's tools = write ``tool_specs()`` there and
# add the module path here; the grouped builder functions above dissolve
# into this list family by family in the follow-up PRs.
_FAMILY_SPEC_MODULES: tuple[str, ...] = (
    "agentic_swmm.agent.tool_handlers.demo",
    "agentic_swmm.agent.tool_handlers.gap_fill",
    "agentic_swmm.agent.tool_handlers.introspection",
    "agentic_swmm.agent.tool_handlers.runtime_ops",
    "agentic_swmm.agent.tool_handlers.swmm_anywhere",
    "agentic_swmm.agent.tool_handlers.swmm_audit",
    "agentic_swmm.agent.tool_handlers.swmm_builder",
    "agentic_swmm.agent.tool_handlers.swmm_calibration",
    "agentic_swmm.agent.tool_handlers.swmm_canada",
    "agentic_swmm.agent.tool_handlers.swmm_climate",
    "agentic_swmm.agent.tool_handlers.swmm_map",
    "agentic_swmm.agent.tool_handlers.swmm_memory",
    "agentic_swmm.agent.tool_handlers.swmm_network",
    "agentic_swmm.agent.tool_handlers.swmm_onboarding",
    "agentic_swmm.agent.tool_handlers.swmm_plot",
    "agentic_swmm.agent.tool_handlers.swmm_report",
    "agentic_swmm.agent.tool_handlers.swmm_review",
    "agentic_swmm.agent.tool_handlers.swmm_rpt",
    "agentic_swmm.agent.tool_handlers.swmm_runner",
    "agentic_swmm.agent.tool_handlers.swmm_storm",
    "agentic_swmm.agent.tool_handlers.swmm_uncertainty",
    "agentic_swmm.agent.tool_handlers.swmm_wq",
    "agentic_swmm.agent.tool_handlers.web",
)


def _family_specs() -> list[ToolSpec]:
    import importlib

    specs: list[ToolSpec] = []
    for module_name in _FAMILY_SPEC_MODULES:
        module = importlib.import_module(module_name)
        specs.extend(module.tool_specs())
    return specs


def _build_tools() -> dict[str, ToolSpec]:
    specs = _registry_native_tools()
    tools = {spec.name: spec for spec in specs}
    for spec in _family_specs():
        if spec.name in tools:
            raise ValueError(
                f"family seam duplicate: tool {spec.name!r} is already "
                "registered by a grouped builder"
            )
        tools[spec.name] = spec
    return tools


# PRD #128 Phase 2 Group C: ``_doctor_tool`` and ``_retrieve_memory_tool``
# moved to ``tool_handlers/introspection.py``. Re-exported here so import
# paths stay stable.


# PRD #128 Phase 2 Group C: HITL / L5 gap-fill governance handlers moved
# to ``tool_handlers/gap_fill.py``. Re-exported here so import paths stay
# stable — ``_is_tty_for_l5`` is monkeypatched by the L5 headless-block
# tests at ``agentic_swmm.agent.tool_registry._is_tty_for_l5`` and that
# path must keep resolving.


# PRD #128 Phase 2 Group C: runtime file/repo/skill ops moved to
# ``tool_handlers/runtime_ops.py`` (together with the
# ``_patch_paths`` / ``_normalize_search_glob`` helpers). Re-exported
# here so import paths stay stable.



# PRD #128: ``_demo_acceptance_tool`` moved to ``tool_handlers/demo.py``.
# Re-exported here so import paths stay stable.


# Water-quality / design-review / report-export handlers
# (PRD_water_quality.md PR3, PRD_design_review.md PR2, PRD_report_export.md PR2).
# All three are direct-subprocess handlers (not MCP-routed).


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


def _summarize_memory_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``summarize_memory`` args to ``swmm-modeling-memory`` MCP schema.

    The MCP server requires both ``runsDir`` and ``outDir``; if the caller
    omits ``out_dir`` we default to ``memory/modeling-memory`` (the same
    default the CLI used).

    C2 (issue #246): ``obsidian_dir`` is now forwarded as ``obsidianDir``
    when present so the Obsidian vault export path reaches the skill.
    """

    runs_dir = call.args.get("runs_dir")
    if not isinstance(runs_dir, str) or not runs_dir.strip():
        return _failure(call, "missing required argument: runs_dir")
    out_dir = call.args.get("out_dir") or "memory/modeling-memory"
    args: dict[str, Any] = {"runsDir": str(runs_dir), "outDir": str(out_dir)}
    if call.args.get("obsidian_dir"):
        args["obsidianDir"] = str(call.args["obsidian_dir"])
    return args


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
# decision record.


# PRD #128 Phase 2 Group A: ``_plot_run_args`` / ``_plot_run_tool``
# moved to ``tool_handlers/swmm_plot.py``. Re-exported at the bottom
# of this file (after all helpers are defined).


# Family spec/handler history for the tools above lives with each
# family module since issue #358 (tool_specs() self-registration); the
# only registry-path compat re-exports that remain are listed in the
# block at the end of this file.




def _list_mcp_servers_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    servers = load_mcp_registry()
    return {"tool": call.name, "args": call.args, "ok": True, "servers": servers, "summary": f"{len(servers)} configured MCP server(s)"}


def _slim_mcp_tool_listing(tools: list, mapped: list, full: bool) -> tuple[list, list]:
    """Default to a names+description listing (ADR token economy).

    Full schemas replayed on every later LLM call were the single
    largest recon payload (~19k/call average measured 2026-08-09);
    select_skill already returns the chosen skill's full contracts, so
    the exploration listing needs only enough to pick a server. Pass
    ``full: true`` to get complete schemas.
    """
    if full:
        return tools, mapped
    slim_tools = [
        {
            "name": t.get("name"),
            "description": str(t.get("description") or "").split(". ")[0][:160],
        }
        for t in tools
        if isinstance(t, dict)
    ]
    return slim_tools, []


# Live finding F-116 (2026-09-03, S48 r3 + S53): the introspection prologue
# asked for 3 s listings; on a machine busy with SWMM sweeps a Node server
# needs longer to start and the listing failed with "process ended before
# sending a complete line". The default is ten seconds; an explicit request
# is still honored (a caller that wants a quick probe keeps that contract).
MCP_LISTING_DEFAULT_TIMEOUT = 10


def _list_mcp_tools_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _mcp_failure(call, f"MCP server not found: {call.args['server']}")
    timeout = int(call.args.get("timeout_seconds") or MCP_LISTING_DEFAULT_TIMEOUT)
    refresh = bool(call.args.get("refresh"))
    ttl = int(call.args.get("cache_ttl_seconds") or mcp_cache.DEFAULT_TTL_SECONDS)
    if not refresh:
        cached = mcp_cache.read_cached_tools(server, ttl_seconds=ttl)
        if cached is not None:
            mapped = [_map_mcp_tool_schema(str(server["name"]), tool) for tool in cached if isinstance(tool, dict)]
            slim_tools, slim_mapped = _slim_mcp_tool_listing(cached, mapped, bool(call.args.get("full")))
            return {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "tools": slim_tools,
                "mapped_tools": slim_mapped,
                "cache": "hit",
                "summary": f"{len(cached)} cached MCP tool(s) on {server['name']} (names only; select_skill or full:true for schemas)",
            }
    try:
        tools = mcp_client.list_tools(str(server["command"]), [str(arg) for arg in server.get("args", [])], timeout=timeout)
    except Exception as exc:
        return _mcp_failure(call, f"MCP tools/list failed: {exc}")
    cache_path = mcp_cache.write_cached_tools(server, tools)
    mapped = [_map_mcp_tool_schema(str(server["name"]), tool) for tool in tools if isinstance(tool, dict)]
    slim_tools, slim_mapped = _slim_mcp_tool_listing(tools, mapped, bool(call.args.get("full")))
    return {
        "tool": call.name,
        "args": call.args,
        "ok": True,
        "tools": slim_tools,
        "mapped_tools": slim_mapped,
        "cache": "refresh" if refresh else "miss",
        "cache_path": str(cache_path),
        "summary": f"{len(tools)} MCP tool(s) on {server['name']} (names only; select_skill or full:true for schemas)",
    }


def _call_mcp_tool_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _mcp_failure(call, f"MCP server not found: {call.args['server']}")
    arguments = call.args.get("arguments") if isinstance(call.args.get("arguments"), dict) else {}
    try:
        result = mcp_client.call_tool(str(server["command"]), [str(arg) for arg in server.get("args", [])], str(call.args["tool"]), arguments)
    except Exception as exc:
        failure = _mcp_failure(call, f"MCP tools/call failed: {exc}", server=str(server["name"]))
        requested = str(call.args.get("tool") or "")
        if "unknown tool" in str(exc).lower() and requested in AgentToolRegistry().names:
            # Live finding F-30 (2026-09-02): the planner sent the in-process
            # read_rpt_summary to an MCP server and got the raw JSON-RPC
            # error back. Name the route instead.
            failure["hint"] = (
                f"{requested} is an in-process tool of this runtime, not an MCP tool: "
                f"call {requested} directly with the same arguments."
            )
        return failure
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"called MCP tool {server['name']}.{call.args['tool']}"}


# MCP failure/schema mapping helpers moved to ``tool_handlers/_shared.py``
# (issue #358 PR A); re-imported at the top of this file.


def _capabilities_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    names = sorted(_build_tools())
    return {"tool": call.name, "args": call.args, "ok": True, "capabilities": capability_summary(names), "summary": "runtime capabilities returned"}


# PRD #128 Phase 2 Group C: ``_workflow_user_prompt`` and
# ``_active_run_dir_from_global_state`` moved to
# ``tool_handlers/workflow_mode.py``. Re-exported above.


RUN_TESTS_HINT = (
    "run_tests runs the repository's own suite under tests/; it does not execute "
    "agent-written files. Scripts the planner writes belong under "
    "<run>/_agent/scripts and are never executed by the runtime."
)


def _run_tests_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    # Live finding F-147 (2026-09-04, S63): the planner wrote
    # runs/.../_agent/test_convert_pipe_units.py and ran it through this tool,
    # which made the test runner a general code-execution channel around the
    # command allowlist. Only the repository's tests/ tree may be named.
    paths = call.args.get("paths")
    test_paths = [str(path) for path in paths] if isinstance(paths, list) and paths else ["tests"]
    tests_root = (repo_root() / "tests").resolve()
    for path in test_paths:
        resolved = _repo_path(path)
        if resolved is None:
            return _failure(call, f"test path must be inside repository: {path}")
        try:
            resolved.resolve().relative_to(tests_root)
        except ValueError:
            return _failure(call, f"test path is outside the repository's tests/ tree: {path}", hint=RUN_TESTS_HINT)
    timeout = int(call.args.get("timeout_seconds") or 120)
    if importlib.util.find_spec("pytest") is None:
        return _failure(call, "pytest is not installed in this environment", hint="pip install pytest, or run the suite from a checkout")
    return _run_process_tool(call, session_dir, [sys.executable, "-m", "pytest", *test_paths], cwd=repo_root(), timeout=timeout)


#: What ``run_allowed_command`` will run, stated once for the description,
#: the denial and the docs. Live finding F-10 (2026-09-02): a denial that
#: said only "not allowlisted" sent the planner through five variants of
#: ``python -c`` and a "continue past failures?" prompt before it gave up.
ALLOWED_COMMAND_FORMS = (
    "pytest <repo test paths>; python -m pytest <repo test paths>; "
    "python -m agentic_swmm.cli <verb ...>; node scripts/<name>.mjs; "
    "swmm5 <inp> <rpt> <out>"
)
_NOT_A_SHELL_HINT = (
    "run_allowed_command is not a shell: python -c, grep, cat, awk and ad-hoc "
    "scripts are refused. For SWMM .rpt data use read_rpt_summary (Link Flow, "
    "Outfall Loading, Node Inflow, Node Flooding, Node Surcharge, Node Depth, "
    "Conduit Surcharge, Subcatchment Runoff); for file contents use read_file "
    "or search_files."
)


def _run_allowed_command_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = call.args.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        return _failure(call, "command must be a non-empty string array")
    if not _command_allowed(command):
        return _failure(
            call,
            f"command is not allowlisted (allowed: {ALLOWED_COMMAND_FORMS})",
            hint=_NOT_A_SHELL_HINT,
        )
    exe = Path(command[0]).name.lower()
    if exe in {"python", "python.exe"} or exe.startswith("python3"):
        # The interpreter running aiswmm is the one the allowlist means;
        # a bare ``python`` is absent on many machines (macOS ships only
        # python3) and raised FileNotFoundError after passing the check.
        command = [sys.executable, *command[1:]]
    timeout = int(call.args.get("timeout_seconds") or 120)
    return _run_process_tool(call, session_dir, command, cwd=repo_root(), timeout=timeout)


# INP/node resolution and plot-option helpers moved to
# ``tool_handlers/_shared.py`` (issue #358 PR A); re-imported at the top
# of this file. ``_plot_selection_options_for_inp`` was deleted outright:
# the dependency map found zero callers anywhere in the repo (the
# ``_inspect_plot_options_tool`` in swmm_plot.py inlines the equivalent
# logic), and this file's old header docstring claiming otherwise was
# stale.


# PRD #128 Phase 2 Group C: ``_patch_paths`` moved to
# ``tool_handlers/runtime_ops.py`` alongside ``_apply_patch_tool``
# (its sole caller). Re-exported above via runtime_ops.


# The run_allowed_command allowlist (security review P1-2), the
# repo-sandboxed file/INP resolvers, and ``_mcp_server`` moved to
# ``tool_handlers/_shared.py`` (issue #358 PR A); re-imported at the top
# of this file. ``_required_repo_dir`` and ``_optional_repo_output_dir``
# were deleted outright: the dependency map found zero callers anywhere
# in the repo or tests (only stale comment mentions).


# ---------------------------------------------------------------------------
# Compat re-exports (issue #358 final sweep)
# ---------------------------------------------------------------------------
#
# The historical family-module re-export block is gone: every family
# self-registers via ``tool_specs()`` and owns its handler symbols.
# Exactly three names keep a registry-path re-export because external
# consumers (tests) import or monkeypatch them through this module; a
# grep sweep on 2026-08-08 found no others. Remove a consumer, remove
# its line here.
from agentic_swmm.agent.tool_handlers.gap_fill import (  # noqa: E402,F401
    _is_tty_for_l5,
)
from agentic_swmm.agent.tool_handlers.runtime_ops import (  # noqa: E402,F401
    _list_dir_tool,
)
from agentic_swmm.agent.tool_handlers.swmm_plot import (  # noqa: E402,F401
    _plot_run_args,
)
