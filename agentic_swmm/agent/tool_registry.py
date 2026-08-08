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


def _audit_patch_onboarding_tools() -> list[ToolSpec]:
    """Run-audit surface (audit_run, apply_patch) and new-case onboarding acceptance."""
    return [
        ToolSpec("audit_run", "Audit a run directory and write deterministic provenance/comparison/note artifacts. Writes stay inside the run directory unless obsidian=true.", _object({"run_dir": {"type": "string"}, "workflow_mode": {"type": "string"}, "objective": {"type": "string"}, "compare_to": {"type": "string", "description": "Optional path to a second run directory; when present, writes comparison.json comparing the two runs."}, "obsidian": {"type": "boolean", "description": "Also mirror the modelling note into the user's local Obsidian vault (~/Documents/Agentic-SWMM-Obsidian-Vault). Default false: agent-path audits have no side effects outside the run directory, matching the CLI's --no-obsidian default (issue #328)."}}, ["run_dir"]), _audit_run_tool),
        ToolSpec("apply_patch", "Apply a unified diff patch to repository files. Writes are repo-only and blocked for .git/.venv/secret paths.", _object({"patch": {"type": "string"}, "allow_evidence_edits": {"type": "boolean"}}, ["patch"]), _apply_patch_tool),
        # New-case onboarding (#246 follow-up rewire): typed tool that applies the
        # user's reply to the onboarding offer the planner hook surfaced.
        # is_read_only=False — acceptance writes transferred parameters to the
        # session context (explicit-flow action per CONTEXT.md invariant 4).
        # ``apply_onboarding`` moved to swmm_onboarding.tool_specs()
        # (issue #358 C2).
    ]


def _builder_climate_tools() -> list[ToolSpec]:
    """INP assembly (swmm-builder) and rainfall/design-storm synthesis (swmm-climate), plus capabilities/demo/doctor."""
    return [
        # ``build_inp`` moved to swmm_builder.tool_specs() (issue #358 C2).
        # C1 (issue #246): build_raingage_section — builds the SWMM [RAINGAGES] section
        # snippet that pairs with a formatted timeseries.
        ToolSpec(
            "build_raingage_section",
            "Build the SWMM [RAINGAGES] section snippet that pairs with a formatted timeseries produced by format_rainfall. "
            "Writes a text fragment (.txt) and a metadata JSON (.json) consumed by build_inp's raingage_json / timeseries_text inputs.",
            _object(
                {
                    "out_text_path": {"type": "string", "description": "Repository-relative path for the output [RAINGAGES] text snippet."},
                    "out_json_path": {"type": "string", "description": "Repository-relative path for the output raingage metadata JSON."},
                    "gage_id": {"type": "string", "description": "SWMM gage ID (default: derived from series_name or station_id)."},
                    "series_name": {"type": "string", "description": "Name of the SWMM TIMESERIES to reference (from format_rainfall output)."},
                    "station_id": {"type": "string", "description": "Station ID; used to resolve the series name from a multi-station JSON."},
                    "rainfall_json_path": {"type": "string", "description": "Path to the rainfall metadata JSON produced by format_rainfall; used to auto-detect series_name and interval."},
                    "rain_format": {"type": "string", "enum": ["INTENSITY", "VOLUME", "CUMULATIVE"], "description": "SWMM rainfall format type."},
                    "interval_min": {"type": "integer", "description": "Rainfall recording interval in minutes."},
                    "scf": {"type": "number", "description": "Snow catch factor (default 1.0)."},
                },
                ["out_text_path", "out_json_path"],
            ),
            _build_raingage_section_tool,
            is_read_only=False,
        ),
        ToolSpec("capabilities", "Describe what this runtime can and cannot access.", _object({}), _capabilities_tool, is_read_only=True),
        ToolSpec("demo_acceptance", "Run the prepared acceptance demo through the Agentic SWMM CLI.", _object({"run_id": {"type": "string"}, "keep_existing": {"type": "boolean"}}), _demo_acceptance_tool),
        ToolSpec("doctor", "Run the built-in Agentic SWMM runtime doctor.", _object({}), _doctor_tool),
        ToolSpec(
            "format_rainfall",
            "Format rainfall CSV or SWMM .dat files into SWMM TIMESERIES text and metadata JSON using the swmm-climate skill. "
            "Supply exactly one input mode: a single CSV (input_csv), a glob pattern for multiple CSVs (input_glob_patterns), or .dat files (input_dat_paths). "
            "Use input_glob_patterns to batch-convert a directory of per-station CSVs; use station_column/series_name_template for multi-station inputs.",
            _object(
                {
                    "input_csv": {"type": "string", "description": "Path to a single rainfall CSV (mutually exclusive with input_glob_patterns and input_dat_paths)."},
                    "input_glob_patterns": {"type": "array", "items": {"type": "string"}, "description": "Glob patterns matching multiple rainfall CSVs (e.g. ['data/rain_*.csv']). Use to batch-convert a directory."},
                    "input_dat_paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to SWMM .dat timeseries files. Cannot be combined with CSV inputs."},
                    "additional_input_csv_paths": {"type": "array", "items": {"type": "string"}, "description": "Additional CSV paths to merge alongside input_csv."},
                    "dat_value_units": {"type": "string", "description": "Units for .dat file values (required when using input_dat_paths)."},
                    "out_json": {"type": "string"},
                    "out_timeseries": {"type": "string"},
                    "series_name": {"type": "string", "description": "Override series name for single-station outputs."},
                    "series_name_template": {"type": "string", "description": "Template for multi-station series names, e.g. '{station_id}_rainfall'."},
                    "timestamp_column": {"type": "string"},
                    "value_column": {"type": "string"},
                    "station_column": {"type": "string", "description": "Column name identifying per-station rows in a wide-format CSV."},
                    "default_station_id": {"type": "string", "description": "Station ID to use when station_column is absent."},
                    "timestamp_format": {"type": "string", "description": "strptime-compatible timestamp format string."},
                    "window_start": {"type": "string", "description": "ISO datetime string; crop input timeseries to start at this time."},
                    "window_end": {"type": "string", "description": "ISO datetime string; crop input timeseries to end at this time."},
                    "value_units": {"type": "string"},
                    "unit_policy": {"type": "string", "enum": ["strict", "convert_to_mm_per_hr"]},
                    "timestamp_policy": {"type": "string", "enum": ["strict", "sort"]},
                },
                ["out_json", "out_timeseries"],
            ),
            _format_rainfall_tool,
        ),
        # PR #256 follow-up: generate_design_storm — MCP-routed via swmm-climate.
        # Use when no measured rainfall exists and you need to synthesise a
        # hyetograph from a return period + IDF coefficients.
        # Contrast with format_rainfall (use when you HAVE measured rainfall data).
        ToolSpec(
            "generate_design_storm",
            "Synthesise a design-storm hyetograph from return period and IDF coefficients when no measured rainfall exists. "
            "Writes SWMM [TIMESERIES] text and metadata JSON that build_inp / build_raingage_section consume unchanged. "
            "Use format_rainfall instead when you have measured rainfall data.",
            _object(
                {
                    "method": {"type": "string", "enum": ["chicago", "alternating_block"], "description": "chicago = Keifer-Chu hyetograph from IDF formula; alternating_block = from explicit IDF table."},
                    "duration_min": {"type": "number", "description": "Total storm duration in minutes."},
                    "out_json": {"type": "string", "description": "Repository-relative path for output metadata JSON."},
                    "out_timeseries": {"type": "string", "description": "Repository-relative path for output SWMM [TIMESERIES] text (.txt or .dat)."},
                    "form": {"type": "string", "enum": ["CN", "generic"], "description": "IDF formula form (chicago only). CN: q=167·A1·(1+C·lgP)/(t+b)^n; generic: i=a/(t+b)^c."},
                    "return_period": {"type": "number", "description": "Return period in years (default 2)."},
                    "dt": {"type": "number", "description": "Timestep in minutes (default 5)."},
                    "r": {"type": "number", "description": "Peak-position ratio for chicago method, 0<r<1 (default 0.4)."},
                    "a1": {"type": "number", "description": "CN form coefficient A1."},
                    "c_coeff": {"type": "number", "description": "CN form coefficient C."},
                    "b": {"type": "number", "description": "Both forms: time-offset coefficient b (min)."},
                    "n": {"type": "number", "description": "CN form exponent n."},
                    "a_coeff": {"type": "number", "description": "Generic form coefficient a."},
                    "c_exp": {"type": "number", "description": "Generic form exponent c."},
                    "idf_csv": {"type": "string", "description": "CSV path with columns duration_min,intensity_mm_per_hr for alternating_block method."},
                    "idf_json": {"type": "string", "description": "Inline JSON list of {duration_min, intensity_mm_per_hr} objects for alternating_block method."},
                    "series_name": {"type": "string", "description": "Override series name token (default TS_DESIGN_P<P>Y_<duration>MIN)."},
                },
                ["method", "duration_min", "out_json", "out_timeseries"],
            ),
            _generate_design_storm_tool,
            is_read_only=False,
        ),
        # Legacy shape-library generator (PRD-06 B.4) — kept alongside the
        # IDF-driven tool above because it covers shapes the IDF path does
        # not (uniform/triangular/front/back/huff/scs) from an EXPLICIT depth.
        ToolSpec("generate_storm_shape", "Generate a SWMM design-storm .dat timeseries from a named hyetograph shape (uniform/triangular/front_loaded/back_loaded/chicago/huff/scs) scaled to an EXPLICIT total depth you already know. Pass shape + out; chicago/triangular take depth_mm + duration_min + peak_position, huff takes quartile (1-4). Use generate_design_storm instead when you only have a return period + IDF coefficients and need the depth derived for you.", _object({"shape": {"type": "string", "enum": ["uniform", "triangular", "front_loaded", "back_loaded", "chicago", "huff", "scs"]}, "out": {"type": "string"}, "depth_mm": {"type": "number"}, "duration_min": {"type": "integer"}, "peak_position": {"type": "number"}, "quartile": {"type": "integer", "enum": [1, 2, 3, 4]}, "idf": {"type": "string"}}, ["shape", "out"]), _generate_storm_shape_tool, is_read_only=False),
    ]


def _introspection_mcp_network_plot_tools() -> list[ToolSpec]:
    """Repo/diff introspection, the MCP bridge, network QA/export, and the plot/map renderers."""
    return [
        ToolSpec("git_diff", "Read the current repository diff or diff stat.", _object({"stat_only": {"type": "boolean"}, "path": {"type": "string"}}), _git_diff_tool, is_read_only=True),
        # ``inspect_plot_options`` / ``plot_run`` moved to
        # swmm_plot.tool_specs(); ``map_run`` to swmm_map.tool_specs()
        # (issue #358 C1).
        ToolSpec("list_dir", "List a repository directory.", _object({"path": {"type": "string"}}), _list_dir_tool, is_read_only=True),
        ToolSpec("list_mcp_servers", "List configured local MCP servers.", _object({}), _list_mcp_servers_tool, is_read_only=True),
        ToolSpec("list_mcp_tools", "List tools exposed by one configured MCP server.", _object({"server": {"type": "string"}, "timeout_seconds": {"type": "integer"}, "refresh": {"type": "boolean"}, "cache_ttl_seconds": {"type": "integer"}}, ["server"]), _list_mcp_tools_tool, is_read_only=True),
        ToolSpec("call_mcp_tool", "Call a tool exposed by a configured local MCP server.", _object({"server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}}, ["server", "tool"]), _call_mcp_tool_tool),
        ToolSpec("list_skills", "List available repository skills.", _object({}), _list_skills_tool, is_read_only=True),
        # ``network_qa`` / ``network_to_inp`` moved to
        # swmm_network.tool_specs() (issue #358 C2).
    ]


def _read_memory_hitl_tools() -> list[ToolSpec]:
    """File/rpt/skill reading, memory recall, and human-in-the-loop expert-review and gap-judgement pauses."""
    return [
        ToolSpec("read_file", "Read a repository file and return a bounded excerpt (capped at 4000 chars). NOTE: for SWMM .rpt summary sections (Link Flow / Outfall Loading / Node Inflow / water-quality sections), use read_rpt_summary instead — read_file's 4000-char cap cannot reach summary sections, which sit past the rpt header in 300+ KB files.", _object({"path": {"type": "string"}}, ["path"]), _read_file_tool, is_read_only=True),
        # ``read_rpt_summary`` moved to swmm_rpt.tool_specs() (issue #358 C1).
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
    ]


def _run_ops_memory_report_tools() -> list[ToolSpec]:
    """SWMM run execution (local, bbox-synthesized, SWMMCanada), allowlisted command/test running, skill selection, memory summarize/retrieve, and the water-quality/design-review/report-export handoffs."""
    return [
        # ``run_swmm_inp`` moved to swmm_runner.tool_specs() (issue #358 C1).
        # ``synth_swmm_from_bbox`` moved to swmm_anywhere.tool_specs()
        # (issue #358 C2).
        # ``fetch_swmm_from_canada`` and ``run_climate_scenarios`` moved to
        # their family modules' ``tool_specs()`` (issue #358 PR B pilots);
        # the family seam in ``_build_tools`` registers them.
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
        ToolSpec("summarize_memory", "Summarize audited runs into the modeling-memory directory.", _object({"runs_dir": {"type": "string"}, "out_dir": {"type": "string"}, "obsidian_dir": {"type": "string", "description": "Optional path to an Obsidian vault directory; when present, the skill writes a Markdown summary there in addition to the standard output."}}, ["runs_dir"]), _summarize_memory_tool),
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
        # --- Water quality -----------------------------------------------
        # PRD_water_quality.md PR3: read_wq_loads ToolSpec.
        # Direct-subprocess handler (no MCP); mirrors retrieve_memory pattern.
        # is_read_only=True: extract_wq_loads.py only reads the rpt and prints
        # JSON to stdout — it never writes files in its default (no --out-json) mode.
        ToolSpec(
            "read_wq_loads",
            "Read pollutant load summaries from a completed run's .rpt file; returns wq_present=false for non-WQ runs.",
            _object(
                {
                    "rpt_path": {"type": "string", "description": "Path to the SWMM .rpt file from a completed run."},
                },
                ["rpt_path"],
            ),
            _read_wq_loads_tool,
            is_read_only=True,
        ),
        # --- Design review -----------------------------------------------
        # PRD_design_review.md PR2: review_run ToolSpec.
        # Direct handler; writes 11_review/ artifacts → is_read_only=False.
        ToolSpec(
            "review_run",
            "Run the deterministic design-review rule checklist against a completed SWMM run; reports findings, never certifies compliance.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Absolute path to the run directory."},
                    "rules": {"type": "string", "description": "Path to a custom YAML rulebook. Omit to use the bundled GB 50014 template."},
                    "out_dir": {"type": "string", "description": "Output directory for review artifacts (default: <run_dir>/11_review/)."},
                },
                ["run_dir"],
            ),
            _review_run_tool,
            is_read_only=False,
        ),
        # --- Report export -----------------------------------------------
        # PRD_report_export.md PR2: generate_report ToolSpec.
        # Direct handler; writes .docx deliverable → is_read_only=False.
        ToolSpec(
            "generate_report",
            "Assemble a client-deliverable Word report (.docx) from an audited run directory. "
            "Reads manifest.json, experiment_provenance.json, model_diagnostics.json, comparison.json, "
            "and any PNG figures — never re-runs SWMM. Output path defaults to <run_dir>/report.docx.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Absolute path to the audited run directory."},
                    "out": {"type": "string", "description": "Output .docx path (default: <run_dir>/report.docx)."},
                    "template": {"type": "string", "description": "Path to a template YAML; omit to use the default template."},
                    "title": {"type": "string", "description": "Override the cover title text."},
                },
                ["run_dir"],
            ),
            _generate_report_tool,
            is_read_only=False,
        ),
    ]


# The six swmm-calibration ToolSpecs moved to
# swmm_calibration.tool_specs() together with
# _CALIBRATION_COMMON_REQUIRED (issue #358 C3).


def _web_uncertainty_tools() -> list[ToolSpec]:
    """Web fetch/search plus the five swmm-uncertainty ToolSpecs (OAT/Morris/Sobol sensitivity, rainfall ensemble, source decomposition)."""
    return [
        # Not is_read_only: fetching a model-chosen URL is network egress (a
        # sensitive effect and an exfiltration channel), so it goes through the
        # approval gate rather than auto-approving (review P1-3).
        ToolSpec("web_fetch_url", "Fetch and summarize a web page. Web evidence is not SWMM run evidence.", _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}), _web_fetch_url_tool),
        ToolSpec("web_search", "Run a lightweight web search and return cited result URLs. Web evidence is not SWMM run evidence.", _object({"query": {"type": "string"}, "allowed_domains": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}}), _web_search_tool, is_read_only=True),
        # dark-MCP registration (PR 2, issue #246): 5 uncertainty tools.
        # All is_read_only=False — each writes artefacts.
        ToolSpec(
            "swmm_sensitivity_oat",
            "OAT sensitivity: perturb each parameter around a baseline and rank by RMSE+peak-error spread.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "base_params": {"type": "string", "description": "JSON object of baseline parameter values."},
                    "scan_spec": {"type": "string", "description": "JSON object: parameter -> list of trial values."},
                },
                _SENSITIVITY_COMMON_REQUIRED + ["base_params", "scan_spec"],
            ),
            _swmm_sensitivity_oat_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_sensitivity_morris",
            "Morris elementary-effects screening via SALib; budget = r*(k+1) SWMM runs.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "parameter_space": {"type": "string", "description": "JSON: parameter -> {min, max} bounds."},
                    "morris_r": {"type": "integer", "description": "Trajectory count; budget = r*(k+1)."},
                    "morris_levels": {"type": "integer"},
                },
                _SENSITIVITY_COMMON_REQUIRED + ["parameter_space"],
            ),
            _swmm_sensitivity_morris_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_sensitivity_sobol",
            "Sobol' variance-decomposition (S_i + S_T_i) via SALib Saltelli sampling; budget = N*(2k+2) runs.",
            _object(
                {
                    **_swmm_uncertainty_common_schema(),
                    "parameter_space": {"type": "string", "description": "JSON: parameter -> {min, max} bounds."},
                    "sobol_n": {"type": "integer", "description": "Saltelli base sample size; budget = N*(2k+2)."},
                },
                _SENSITIVITY_COMMON_REQUIRED + ["parameter_space"],
            ),
            _swmm_sensitivity_sobol_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_rainfall_ensemble",
            "Generate a rainfall ensemble (perturbation of observed series or IDF design storms); optionally run swmm5 per realisation.",
            _object(
                {
                    "method": {"type": "string", "enum": ["perturbation", "idf"], "description": "Ensemble generation method."},
                    "config": {"type": "string", "description": "Path to JSON config (see skills/swmm-uncertainty/examples/)."},
                    "run_root": {"type": "string", "description": "Output root; summary at <run_root>/09_audit/rainfall_ensemble_summary.json."},
                    "base_inp": {"type": "string", "description": "If provided, each realisation is patched into this INP and run through swmm5."},
                    "series_name": {"type": "string"},
                    "swmm_node": {"type": "string"},
                    "seed": {"type": "integer"},
                    "dry_run": {"type": "boolean", "description": "Generate realisations but skip swmm5."},
                },
                ["method", "config", "run_root"],
            ),
            _swmm_rainfall_ensemble_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_uncertainty_source_decomposition",
            "Integrate existing 09_audit/ artefacts (Sobol'/Morris/DREAM-ZS/SCE-UA/ensemble) into uncertainty_source_summary.md.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Run directory containing 09_audit/."},
                },
                ["run_dir"],
            ),
            _swmm_uncertainty_source_decomposition_tool,
            is_read_only=False,
        ),
    ]


# Family self-registration seam (issue #358 PR B). A tool_handlers
# family module listed here defines ``tool_specs() -> list[ToolSpec]``
# next to its handlers, importing ToolSpec from ``agent.types`` and the
# schema/path helpers from ``tool_handlers/_shared`` — never from this
# registry. Adding a family's tools = write ``tool_specs()`` there and
# add the module path here; the grouped builder functions above dissolve
# into this list family by family in the follow-up PRs.
_FAMILY_SPEC_MODULES: tuple[str, ...] = (
    "agentic_swmm.agent.tool_handlers.swmm_anywhere",
    "agentic_swmm.agent.tool_handlers.swmm_builder",
    "agentic_swmm.agent.tool_handlers.swmm_calibration",
    "agentic_swmm.agent.tool_handlers.swmm_canada",
    "agentic_swmm.agent.tool_handlers.swmm_climate",
    "agentic_swmm.agent.tool_handlers.swmm_map",
    "agentic_swmm.agent.tool_handlers.swmm_network",
    "agentic_swmm.agent.tool_handlers.swmm_onboarding",
    "agentic_swmm.agent.tool_handlers.swmm_plot",
    "agentic_swmm.agent.tool_handlers.swmm_rpt",
    "agentic_swmm.agent.tool_handlers.swmm_runner",
)


def _family_specs() -> list[ToolSpec]:
    import importlib

    specs: list[ToolSpec] = []
    for module_name in _FAMILY_SPEC_MODULES:
        module = importlib.import_module(module_name)
        specs.extend(module.tool_specs())
    return specs


def _build_tools() -> dict[str, ToolSpec]:
    specs = (
        _audit_patch_onboarding_tools()
        + _builder_climate_tools()
        + _introspection_mcp_network_plot_tools()
        + _read_memory_hitl_tools()
        + _run_ops_memory_report_tools()
        + _web_uncertainty_tools()
    )
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


# Water-quality / design-review / report-export handlers
# (PRD_water_quality.md PR3, PRD_design_review.md PR2, PRD_report_export.md PR2).
# All three are direct-subprocess handlers (not MCP-routed).
from agentic_swmm.agent.tool_handlers.swmm_wq import (  # noqa: E402,F401
    _read_wq_loads_tool,
)
from agentic_swmm.agent.tool_handlers.swmm_review import (  # noqa: E402,F401
    _review_run_tool,
)
from agentic_swmm.agent.tool_handlers.swmm_report import (  # noqa: E402,F401
    _generate_report_tool,
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
# decision record.


# PRD #128 Phase 2 Group A: ``_plot_run_args`` / ``_plot_run_tool``
# moved to ``tool_handlers/swmm_plot.py``. Re-exported at the bottom
# of this file (after all helpers are defined).


# PRD #128 Phase 2 Group B: ``_network_qa_args`` / ``_network_qa_tool``
# and ``_network_to_inp_args`` / ``_network_to_inp_tool`` moved to
# ``tool_handlers/swmm_network.py``. Re-exported here so import paths
# stay stable for ``_build_tools`` and downstream code.
# Handlers self-register via tool_specs() since issue #358 C2; the
# *_args mappers stay re-exported for historical import sites.
from agentic_swmm.agent.tool_handlers.swmm_network import (  # noqa: E402,F401
    _network_qa_args,
    _network_to_inp_args,
)


# PRD #128 Phase 2 Group B: ``_format_rainfall_args`` / ``_format_rainfall_tool``
# moved to ``tool_handlers/swmm_climate.py``. Re-exported here so import
# paths stay stable for ``_build_tools`` and downstream code.
# C1 (issue #246): ``_build_raingage_section_tool`` also imported here.
# PR #256 follow-up: ``_generate_design_storm_tool`` MCP-routed via swmm-climate.
from agentic_swmm.agent.tool_handlers.swmm_climate import (  # noqa: E402,F401
    _build_raingage_section_args,
    _build_raingage_section_tool,
    _format_rainfall_args,
    _format_rainfall_tool,
    _generate_design_storm_tool,
)

# The legacy in-process shape-library generator (PRD-06 B.4, ``aiswmm storm``)
# stays registered under ``generate_storm_shape`` — it covers explicit-depth
# shapes (uniform/triangular/huff/scs) the IDF-driven tool does not.
from agentic_swmm.agent.tool_handlers.swmm_storm import (  # noqa: E402,F401
    _generate_design_storm_tool as _generate_storm_shape_tool,
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


# MCP failure/schema mapping helpers moved to ``tool_handlers/_shared.py``
# (issue #358 PR A); re-imported at the top of this file.


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


# PRD #128 Phase 2 Group A: family-module re-exports.
#
# These imports sit at the very end of this file so the handler symbols
# they re-export are bound on the partial ``tool_registry`` module by
# the time ``_build_tools()`` references them. Since issue #358 PR A the
# leaf helpers the family modules pull back in (``_resolve_inp_for_run``,
# ``_node_suggestions``, ``_node_attribute_options``,
# ``_resolve_existing_inp``, ``_required_repo_file``) live in
# ``tool_handlers/_shared.py`` and are re-imported at the TOP of this
# file, so those lazy back-imports resolve without depending on this
# block at all; family modules can equally import them from ``_shared``
# directly, which is where the follow-up PRs take them.
# Re-exporting the handler symbols here keeps ``_build_tools()`` and
# any existing ``from agentic_swmm.agent.tool_registry import _*_args``
# call sites working byte-for-byte after the move.
# swmm_runner / swmm_plot handler symbols left this block in issue #358
# C1 (they self-register via tool_specs()); the *_args mappers stay
# re-exported for historical import sites.
from agentic_swmm.agent.tool_handlers.swmm_runner import (  # noqa: E402,F401
    _run_swmm_inp_args,
)
from agentic_swmm.agent.tool_handlers.swmm_plot import (  # noqa: E402,F401
    _plot_run_args,
)
# swmm_builder's handler and swmm_anywhere's handler self-register via
# tool_specs() since issue #358 C2; only the args mapper keeps its
# re-export.
from agentic_swmm.agent.tool_handlers.swmm_builder import (  # noqa: E402,F401
    _build_inp_args,
)
# ``swmm_canada`` / ``swmm_climate`` no longer appear in this block:
# they are the issue #358 PR B pilot families and self-register through
# ``_FAMILY_SPEC_MODULES`` / ``tool_specs()`` instead.
# ``map_run`` is a thin CLI wrapper (``aiswmm map``) — no MCP routing,
# no late-import dance. Sibling of ``aiswmm plot`` at the CLI level;
# sibling of ``plot_run`` at the LLM-facing-tool level.
# swmm_map / swmm_rpt left this block in issue #358 C1: their only
# imported symbols were the handlers, which now self-register via
# tool_specs().
# Note: ``_generate_design_storm_tool`` imported above with swmm_climate tools.
# dark-MCP registration (PR 1, issue #246): 6 calibration tools registered
# as first-class typed ToolSpecs so the LLM planner can select them by name.
# The handler module uses the same lazy-import dance as swmm_runner / swmm_plot.
# Handlers and the common schema self-register via tool_specs() since
# issue #358 C3; the *_args mappers stay re-exported for historical
# import sites (tests).
from agentic_swmm.agent.tool_handlers.swmm_calibration import (  # noqa: E402,F401
    _calibrate_args,
    _calibrate_dream_zs_args,
    _calibrate_search_args,
    _calibrate_sceua_args,
    _sensitivity_scan_args,
    _validate_args,
)
# dark-MCP registration (PR 2, issue #246): 5 uncertainty tools.
from agentic_swmm.agent.tool_handlers.swmm_uncertainty import (  # noqa: E402,F401
    _swmm_uncertainty_common_schema,
    _SENSITIVITY_REQUIRED as _SENSITIVITY_COMMON_REQUIRED,
    # args mappers
    _sensitivity_oat_args,
    _sensitivity_morris_args,
    _sensitivity_sobol_args,
    _rainfall_ensemble_args,
    _source_decomposition_args,
    # handler objects
    _swmm_sensitivity_oat_tool,
    _swmm_sensitivity_morris_tool,
    _swmm_sensitivity_sobol_tool,
    _swmm_rainfall_ensemble_tool,
    _swmm_uncertainty_source_decomposition_tool,
)


# swmm_onboarding self-registers via tool_specs() since issue #358 C2.


