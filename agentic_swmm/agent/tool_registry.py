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
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _make_mcp_routed_handler,
    _wrap_mcp_result,
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


# Required args shared by all six calibration ToolSpecs (mirrors
# ``_COMMON_REQUIRED`` in ``tool_handlers/swmm_calibration.py``).
_CALIBRATION_COMMON_REQUIRED = [
    "base_inp", "patch_map", "observed", "run_root", "summary_json"
]


def _build_tools() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec("audit_run", "Audit a run directory and write deterministic provenance/comparison/note artifacts.", _object({"run_dir": {"type": "string"}, "workflow_mode": {"type": "string"}, "objective": {"type": "string"}, "compare_to": {"type": "string", "description": "Optional path to a second run directory; when present, writes comparison.json comparing the two runs."}}, ["run_dir"]), _audit_run_tool),
        ToolSpec("apply_patch", "Apply a unified diff patch to repository files. Writes are repo-only and blocked for .git/.venv/secret paths.", _object({"patch": {"type": "string"}, "allow_evidence_edits": {"type": "boolean"}}, ["patch"]), _apply_patch_tool),
        # New-case onboarding (#246 follow-up rewire): typed tool that applies the
        # user's reply to the onboarding offer the planner hook surfaced.
        # is_read_only=False — acceptance writes transferred parameters to the
        # session context (explicit-flow action per CONTEXT.md invariant 4).
        ToolSpec(
            "apply_onboarding",
            (
                "Apply a user's reply to the new-case onboarding offer. "
                "CALL WHEN: the planner has surfaced an onboarding offer (via the "
                "<onboarding_offer> system-prompt block) and the user has replied. "
                "Pass the user's exact reply as 'response'; the tool classifies it "
                "as accept / decline / customize and returns the applied parameters "
                "and source memory ids. "
                "On accept, transferred parameters from a similar watershed are "
                "applied to the session and reported so you can stamp "
                "memories_applied on the run."
            ),
            _object(
                {
                    "case_name": {
                        "type": "string",
                        "description": "The case name the onboarding offer was made for.",
                    },
                    "response": {
                        "type": "string",
                        "description": (
                            "The user's natural-language reply: "
                            "Y / yes / empty to accept, "
                            "n / no to decline, "
                            "'customize' or 'c' to enter custom mode, "
                            "or any free-form text."
                        ),
                    },
                },
                ["case_name", "response"],
            ),
            _apply_onboarding_tool,
            is_read_only=False,
        ),
        ToolSpec("build_inp", "Assemble a SWMM INP from explicit CSV/JSON/text inputs using the swmm-builder skill.", _object({"subcatchments_csv": {"type": "string"}, "params_json": {"type": "string"}, "network_json": {"type": "string"}, "rainfall_json": {"type": "string"}, "raingage_json": {"type": "string"}, "timeseries_text": {"type": "string"}, "config_json": {"type": "string"}, "default_gage_id": {"type": "string"}, "water_quality_json": {"type": "string", "description": "Optional path to a WQ config JSON enabling pollutant buildup/washoff simulation ([POLLUTANTS]/[LANDUSES]/[BUILDUP]/[WASHOFF]/[COVERAGES]/[LOADINGS] sections)."}, "out_inp": {"type": "string"}, "out_manifest": {"type": "string"}}, ["subcatchments_csv", "params_json", "network_json", "out_inp", "out_manifest"]), _build_inp_tool),
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
        ToolSpec("git_diff", "Read the current repository diff or diff stat.", _object({"stat_only": {"type": "boolean"}, "path": {"type": "string"}}), _git_diff_tool, is_read_only=True),
        ToolSpec("inspect_plot_options", "Inspect a run directory or INP file and return selectable rainfall series, nodes, and node output attributes for plotting.", _object({"run_dir": {"type": "string"}, "inp_path": {"type": "string"}, "out_file": {"type": "string"}}, []), _inspect_plot_options_tool, is_read_only=True),
        ToolSpec("list_dir", "List a repository directory.", _object({"path": {"type": "string"}}), _list_dir_tool, is_read_only=True),
        ToolSpec("list_mcp_servers", "List configured local MCP servers.", _object({}), _list_mcp_servers_tool, is_read_only=True),
        ToolSpec("list_mcp_tools", "List tools exposed by one configured MCP server.", _object({"server": {"type": "string"}, "timeout_seconds": {"type": "integer"}, "refresh": {"type": "boolean"}, "cache_ttl_seconds": {"type": "integer"}}, ["server"]), _list_mcp_tools_tool, is_read_only=True),
        ToolSpec("call_mcp_tool", "Call a tool exposed by a configured local MCP server.", _object({"server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}}, ["server", "tool"]), _call_mcp_tool_tool),
        ToolSpec("list_skills", "List available repository skills.", _object({}), _list_skills_tool, is_read_only=True),
        ToolSpec("map_run", "Render the spatial network layout (subcatchments + conduits + outfalls) of a SWMM model as a PNG. Sibling of plot_run: plot_run draws the rainfall-runoff hydrograph; map_run draws the network map. Auto-discovers the INP from the run directory; pass inp to override.", _object({"run_dir": {"type": "string"}, "inp": {"type": "string"}, "out_png": {"type": "string"}, "dpi": {"type": "integer"}, "no_subcatchments": {"type": "boolean"}, "no_vertices": {"type": "boolean"}}, ["run_dir"]), _map_run_tool),
        ToolSpec("network_qa", "Validate a SWMM network JSON using the swmm-network QA script.", _object({"network_json": {"type": "string"}, "report_json": {"type": "string"}}, ["network_json"]), _network_qa_tool),
        ToolSpec("network_to_inp", "Export a SWMM network JSON to INP section text using the swmm-network script.", _object({"network_json": {"type": "string"}, "out_path": {"type": "string"}}, ["network_json", "out_path"]), _network_to_inp_tool),
        ToolSpec(
            "plot_run",
            "Create a rainfall + flow hydrograph plot from a run directory. "
            "The lower panel renders EITHER a node attribute (when 'node' is supplied — typical for an outfall rain-runoff hydrograph) "
            "OR a conduit Flow_rate time series (when 'link' is supplied — for a pipe/conduit hydrograph). "
            "'node' and 'link' are mutually exclusive; pass one. "
            "Pick conduit IDs from the .rpt Link Flow Summary; pick node IDs from inspect_plot_options. "
            "Optional day-window cropping: supply focus_day (YYYY-MM-DD) to crop to one day; "
            "window_start and window_end (HH:MM) further narrow to a sub-day window — "
            "both require focus_day (the server rejects window_start/window_end without focus_day).",
            _object(
                {
                    "run_dir": {"type": "string"},
                    "node": {"type": "string"},
                    "node_attr": {"type": "string"},
                    "link": {"type": "string"},
                    "rain_ts": {"type": "string"},
                    "rain_kind": {"type": "string", "enum": ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"]},
                    "out_png": {"type": "string"},
                    "focus_day": {"type": "string", "description": "Crop plot axis to this day (YYYY-MM-DD format)."},
                    "window_start": {"type": "string", "description": "Sub-day window start (HH:MM). Only valid together with focus_day; rejected without it."},
                    "window_end": {"type": "string", "description": "Sub-day window end (HH:MM). Only valid together with focus_day; rejected without it."},
                },
                ["run_dir"],
            ),
            _plot_run_tool,
        ),
        ToolSpec("read_file", "Read a repository file and return a bounded excerpt (capped at 4000 chars). NOTE: for SWMM .rpt summary sections (Link Flow / Outfall Loading / Node Inflow / water-quality sections), use read_rpt_summary instead — read_file's 4000-char cap cannot reach summary sections, which sit past the rpt header in 300+ KB files.", _object({"path": {"type": "string"}}, ["path"]), _read_file_tool, is_read_only=True),
        ToolSpec(
            "read_rpt_summary",
            (
                "Parse a structured summary section from a SWMM .rpt file. "
                "AVAILABLE SECTIONS (the 'section' enum): "
                "'Link Flow Summary' = every conduit's peak flow / time-of-peak / Max-Full ratio (use to find the busiest pipe); "
                "'Outfall Loading Summary' = every outfall node's flow frequency / avg / max / total volume + pollutant loads when water quality is enabled; "
                "'Node Inflow Summary' = every node's lateral and total inflow (use for upstream-network diagnostics); "
                "'Runoff Quality Continuity' = pollutant mass balance at the land surface (one column per pollutant, kg); "
                "'Quality Routing Continuity' = pollutant mass balance through the pipe network (one column per pollutant, kg); "
                "'Subcatchment Washoff Summary' = total pollutant load washed off each subcatchment (kg per pollutant); "
                "'Link Pollutant Load Summary' = total pollutant load transported through each link (kg per pollutant). "
                "CALL THIS TOOL ONCE PER SECTION YOU NEED — the tool is stateless, so issuing multiple calls with different 'section' values is the correct and cheap pattern; do NOT try to fetch 'Outfall Loading' by re-reading the rpt with read_file. "
                "Returns top N rows (default 5) as typed JSON objects sorted by the per-section peak/max column. "
                "USE THIS, NOT read_file or search_files, for ALL .rpt data extraction in agent flows."
            ),
            _object({
                "rpt_path": {"type": "string"},
                "section": {"type": "string", "enum": [
                    "Link Flow Summary",
                    "Outfall Loading Summary",
                    "Node Inflow Summary",
                    "Runoff Quality Continuity",
                    "Quality Routing Continuity",
                    "Subcatchment Washoff Summary",
                    "Link Pollutant Load Summary",
                ]},
                "top_n": {"type": "integer"},
                "sort_by": {"type": "string"},
            }, ["rpt_path", "section"]),
            _read_rpt_summary_tool,
            is_read_only=True,
        ),
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
                    "config_overrides": {
                        "type": "object",
                        "description": (
                            "Per-call SWMManywhere parameter overrides, shape {group: {param: value}}, "
                            "merged onto the resolved config. Use to fix structural complaints from "
                            "network_qa, then re-synthesise: LOWER outfall_derivation.outfall_length "
                            "(default 200) for more outfalls / fewer orphan / no-outfall-path nodes; "
                            "RAISE subcatchment_derivation.node_merge_distance (default 10, keep < "
                            "max_street_length) or max_street_length (default 60) for fewer pipes. "
                            "Shortcut: pass upstream_defaults=true to drop aiswmm's tuned outfall "
                            "overrides entirely. Full symptom->knob table in swmm-anywhere SKILL.md."
                        ),
                    },
                },
                ["bbox"],
            ),
            _synth_swmm_from_bbox_tool,
        ),
        ToolSpec(
            "fetch_swmm_from_canada",
            (
                "Fetch a SWMM .inp built from REAL municipal pipe networks for a "
                "supported Canadian city, via the SWMMCanada upstream HTTP service "
                "(ADR-0001).\n"
                "USE WHEN: the user wants a model for a Canadian location covered by "
                "published municipal storm-pipe data (Victoria, Ottawa, Calgary, "
                "Surrey, London, Kitchener-Waterloo, Kelowna) and wants the real "
                "network, not a synthesized one.\n"
                "DO NOT USE WHEN: the AOI is outside Canada or outside a supported "
                "city — use synth_swmm_from_bbox (global, synthesized) instead.\n"
                "Models are uncalibrated first-pass estimates — treat like the synth "
                "path (reference-free QA only). Requires the SWMMCanada service URL "
                "(AISWMM_SWMMCANADA_URL); returns a stage-tagged hint if unset."
            ),
            _object(
                {
                    "aoi_geojson": {
                        "type": "string",
                        "description": "GeoJSON Polygon string for the area of interest. Provide this or bbox.",
                    },
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": "WGS84 bounding box [min_lon, min_lat, max_lon, max_lat]; converted to a polygon. Provide this or aoi_geojson.",
                    },
                    "start_date": {"type": "string", "description": "Rainfall window start, ISO YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Rainfall window end, ISO YYYY-MM-DD."},
                    "run_dir": {"type": "string"},
                    "base_url": {
                        "type": "string",
                        "description": "Override the SWMMCanada service base URL (else $AISWMM_SWMMCANADA_URL).",
                    },
                },
                ["start_date", "end_date"],
            ),
            fetch_swmm_from_canada_tool,
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
        # Direct handler; writes 09_review/ artifacts → is_read_only=False.
        ToolSpec(
            "review_run",
            "Run the deterministic design-review rule checklist against a completed SWMM run; reports findings, never certifies compliance.",
            _object(
                {
                    "run_dir": {"type": "string", "description": "Absolute path to the run directory."},
                    "rules": {"type": "string", "description": "Path to a custom YAML rulebook. Omit to use the bundled GB 50014 template."},
                    "out_dir": {"type": "string", "description": "Output directory for review artifacts (default: <run_dir>/09_review/)."},
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
        # ---------------------------------------------------------------
        # swmm-calibration tools (dark-MCP registration — PR 1, issue #246)
        # All is_read_only=False: calibration runs SWMM and writes files.
        # ---------------------------------------------------------------
        ToolSpec(
            "swmm_sensitivity_scan",
            (
                "Score a set of explicit candidate parameter sets against observed flow data and rank them by "
                "the chosen objective (NSE/KGE/RMSE/…). Use this tool when you already have a fixed list of "
                "candidate parameter combinations (e.g. from a previous search or expert knowledge) and want "
                "to evaluate and rank them without any new search. "
                "Inputs required: observed flow CSV (observed), patchable INP (base_inp), patch-map JSON (patch_map), "
                "parameter-sets JSON (parameter_sets), run root directory (run_root), output summary path (summary_json). "
                "Compare with swmm_calibrate (same scan but also promotes the best candidate), "
                "swmm_calibrate_search (bounded search over a parameter space), "
                "swmm_calibrate_sceua (global SCE-UA optimisation), and "
                "swmm_calibrate_dream_zs (Bayesian posterior)."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "parameter_sets": {"type": "string", "description": "Path to a JSON file listing the explicit candidate parameter sets to evaluate."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["parameter_sets"],
            ),
            _swmm_sensitivity_scan_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_calibrate",
            (
                "Evaluate an explicit set of candidate parameter combinations against observed flow data, report "
                "the best-scoring candidate, and optionally write the best parameters to a JSON file for downstream use. "
                "Use this tool when you have a fixed candidate list and want to identify and optionally promote the best "
                "parameter set in one step. "
                "Inputs required: observed flow CSV (observed), patchable INP (base_inp), patch-map JSON (patch_map), "
                "parameter-sets JSON (parameter_sets), run root directory (run_root), output summary path (summary_json). "
                "Compare with swmm_sensitivity_scan (ranks all candidates without promotion), "
                "swmm_calibrate_search (generates candidates via random/LHS/adaptive search), "
                "swmm_calibrate_sceua (global optimisation), and "
                "swmm_calibrate_dream_zs (Bayesian posterior)."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "parameter_sets": {"type": "string", "description": "Path to a JSON file listing the explicit candidate parameter sets to evaluate."},
                    "best_params_out": {"type": "string", "description": "Optional path to write the best parameter set as a JSON file."},
                    "candidate_run_dir": {"type": "string", "description": "Optional path to an existing candidate run directory to promote as the accepted calibration run."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["parameter_sets"],
            ),
            _swmm_calibrate_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_calibrate_search",
            (
                "Run a bounded, reproducible calibration search over a parameter space using random sampling, "
                "Latin Hypercube Sampling (LHS), or adaptive multi-round refinement. Use this tool when you have "
                "a parameter space definition (search_space JSON) and want the agent to generate and evaluate "
                "candidate sets automatically. Produces calibration_summary.json with the best-found parameters. "
                "Inputs required: observed flow CSV (observed), patchable INP (base_inp), patch-map JSON (patch_map), "
                "parameter space JSON (search_space), run root directory (run_root), output summary path (summary_json). "
                "This is the recommended starting point for model calibration. "
                "Use swmm_calibrate_sceua for global SCE-UA optimisation (requires spotpy) or "
                "swmm_calibrate_dream_zs for Bayesian posterior estimation (requires spotpy). "
                "Use swmm_validate after finding good parameters to score them on a hold-out event."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "search_space": {"type": "string", "description": "Path to a JSON file defining the calibration parameter space (name, min, max per parameter)."},
                    "strategy": {"type": "string", "enum": ["random", "lhs", "adaptive"], "description": "Sampling strategy: 'random' (Monte Carlo), 'lhs' (Latin Hypercube, default), or 'adaptive' (multi-round refinement around the elite fraction)."},
                    "iterations": {"type": "integer", "description": "Number of candidate evaluations per round (default: 12)."},
                    "rounds": {"type": "integer", "description": "Number of search rounds (default: 1; increase for adaptive multi-round refinement)."},
                    "seed": {"type": "integer", "description": "Random seed for reproducibility (default: 42)."},
                    "elite_fraction": {"type": "number", "description": "Fraction of top candidates kept as elite for adaptive refinement (default: 0.3)."},
                    "refine_margin": {"type": "number", "description": "Margin around elite parameters for adaptive space contraction (default: 0.1)."},
                    "min_span_fraction": {"type": "number", "description": "Minimum search-space span as a fraction of original range (default: 0.1)."},
                    "best_params_out": {"type": "string", "description": "Optional path to write the best parameter set as a JSON file."},
                    "candidate_run_dir": {"type": "string", "description": "Optional path to an existing candidate run directory to promote as the accepted calibration run."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["search_space"],
            ),
            _swmm_calibrate_search_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_calibrate_sceua",
            (
                "Run global Shuffled Complex Evolution (SCE-UA) calibration with KGE as the primary objective. "
                "Produces calibration_summary.json with primary_value, full KGE decomposition (r, alpha, beta), "
                "secondary metrics (NSE, PBIAS%, RMSE, peak-flow error, peak-timing error), and a convergence.csv trace. "
                "Use this tool for publication-grade global optimisation when a bounded random search (swmm_calibrate_search) "
                "has already identified a plausible region. Requires the optional 'spotpy' Python dependency. "
                "Inputs required: observed flow CSV (observed), patchable INP (base_inp), patch-map JSON (patch_map), "
                "parameter space JSON (search_space), run root directory (run_root), output summary path (summary_json). "
                "Compare with swmm_calibrate_search (no spotpy needed, faster), "
                "swmm_calibrate_dream_zs (Bayesian posterior with convergence diagnostics). "
                "Use swmm_validate after calibration to score the accepted parameters on a hold-out event."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "search_space": {"type": "string", "description": "Path to a JSON file defining the calibration parameter space (name, min, max per parameter)."},
                    "iterations": {"type": "integer", "description": "Total SCE-UA function evaluations budget (default: 200)."},
                    "seed": {"type": "integer", "description": "Random seed for reproducibility (default: 42)."},
                    "sceua_ngs": {"type": "integer", "description": "Number of SCE-UA complexes (default: 4; heuristic is 2*p+1 where p = number of parameters)."},
                    "best_params_out": {"type": "string", "description": "Optional path to write the best parameter set as a JSON file."},
                    "convergence_csv": {"type": "string", "description": "Optional path to write the per-iteration KGE trace (default: alongside summary_json)."},
                    "candidate_run_dir": {"type": "string", "description": "Optional path to an existing candidate run directory to promote as the accepted calibration run."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["search_space"],
            ),
            _swmm_calibrate_sceua_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_calibrate_dream_zs",
            (
                "Run DREAM-ZS Bayesian calibration with a KGE-based likelihood (exp(-0.5 * (1 - KGE) / sigma^2)). "
                "Writes five posterior artefacts: posterior_samples.csv (post-burn-in MCMC chains), best_params.json "
                "(MAP estimate), chain_convergence.json (Gelman-Rubin Rhat per parameter), per-parameter marginal "
                "histograms, and a parameter correlation matrix PNG. The calibration_summary.json includes a "
                "posterior_summary block with chain count, Rhat values, and per-parameter quantiles. "
                "Use this tool when parameter uncertainty quantification and convergence diagnostics are required "
                "alongside the best-fit calibration. Requires the optional 'spotpy' Python dependency. "
                "Inputs required: observed flow CSV (observed), patchable INP (base_inp), patch-map JSON (patch_map), "
                "parameter space JSON (search_space), run root directory (run_root), output summary path (summary_json). "
                "Compare with swmm_calibrate_sceua (deterministic global optimisation, no posterior) and "
                "swmm_calibrate_search (no spotpy needed). "
                "Use swmm_validate after calibration to score accepted parameters on a hold-out event."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "search_space": {"type": "string", "description": "Path to a JSON file defining the calibration parameter space (name, min, max per parameter)."},
                    "iterations": {"type": "integer", "description": "Total MCMC iterations across all chains (default: 1000)."},
                    "seed": {"type": "integer", "description": "Random seed for reproducibility (default: 42)."},
                    "dream_chains": {"type": "integer", "description": "Number of MCMC chains (>=2 for Gelman-Rubin Rhat; default: 4)."},
                    "dream_sigma": {"type": "number", "description": "Likelihood width sigma on (1-KGE) (default: 0.1)."},
                    "dream_rhat_threshold": {"type": "number", "description": "Gelman-Rubin Rhat convergence threshold (default: 1.2)."},
                    "dream_runs_after_convergence": {"type": "integer", "description": "Extra posterior samples to draw after convergence is detected (default: 50)."},
                    "dream_output_dir": {"type": "string", "description": "Optional audit directory for the 5 DREAM-ZS artefacts (defaults to the parent of summary_json)."},
                    "best_params_out": {"type": "string", "description": "Optional path to write the MAP parameter set as a JSON file."},
                    "candidate_run_dir": {"type": "string", "description": "Optional path to an existing candidate run directory to promote as the accepted calibration run."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["search_space"],
            ),
            _swmm_calibrate_dream_zs_tool,
            is_read_only=False,
        ),
        ToolSpec(
            "swmm_validate",
            (
                "Apply one accepted parameter set to a second (hold-out) event and score the validation run. "
                "Use this tool as the final step in a calibration workflow: after swmm_calibrate_search, "
                "swmm_calibrate_sceua, or swmm_calibrate_dream_zs identifies the best parameters, run "
                "swmm_validate with a different observed-flow period to confirm the parameters generalise. "
                "Inputs required: observed flow CSV for the hold-out event (observed), patchable INP (base_inp), "
                "patch-map JSON (patch_map), accepted parameter JSON (best_params), run root directory (run_root), "
                "output summary path (summary_json). "
                "Compare with swmm_calibrate / swmm_calibrate_search (which score the calibration event, not the validation event)."
            ),
            _object(
                {
                    **_swmm_calibrate_common_schema(),
                    "best_params": {"type": "string", "description": "Path to the accepted parameter JSON (output of best_params_out from a prior calibration run)."},
                    "trial_name": {"type": "string", "description": "Label for this validation trial used in output file names (default: 'validation')."},
                },
                _CALIBRATION_COMMON_REQUIRED + ["best_params"],
            ),
            _swmm_validate_tool,
            is_read_only=False,
        ),
        ToolSpec("web_fetch_url", "Fetch and summarize a web page. Web evidence is not SWMM run evidence.", _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}), _web_fetch_url_tool, is_read_only=True),
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
from agentic_swmm.agent.tool_handlers.swmm_network import (  # noqa: E402,F401
    _network_qa_args,
    _network_qa_tool,
    _network_to_inp_args,
    _network_to_inp_tool,
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
# family modules pull back in (``_resolve_inp_for_run``,
# ``_node_suggestions``, ``_node_attribute_options``,
# ``_resolve_existing_inp``, ``_required_repo_file``,
# ``_required_repo_dir``, ``_optional_repo_output_dir``) is already
# bound on the partial ``tool_registry`` module by the time the family
# submodules execute their ``from agentic_swmm.agent.tool_registry
# import ...`` statements. (``_make_mcp_routed_handler`` no longer
# rides this cycle — it lives in ``tool_handlers/_shared.py`` and the
# family modules import it from there directly.)
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
# ADR-0001: ``swmm-canada`` handler drives the SWMMCanada HTTP service
# (real municipal-pipe INP source). Pure-stdlib client, in-process — same
# clean re-export shape as swmm-anywhere, no MCP routing.
from agentic_swmm.agent.tool_handlers.swmm_canada import (  # noqa: E402,F401
    fetch_swmm_from_canada_tool,
)
# ``map_run`` is a thin CLI wrapper (``aiswmm map``) — no MCP routing,
# no late-import dance. Sibling of ``aiswmm plot`` at the CLI level;
# sibling of ``plot_run`` at the LLM-facing-tool level.
from agentic_swmm.agent.tool_handlers.swmm_map import (  # noqa: E402,F401
    _map_run_tool,
)
# ``read_rpt_summary`` is an in-process rpt parser — no CLI verb, no
# MCP. Sits next to ``swmm_map`` in the late-import block because the
# handler late-imports ``_required_repo_file`` from this module.
from agentic_swmm.agent.tool_handlers.swmm_rpt import (  # noqa: E402,F401
    _read_rpt_summary_tool,
)
# Note: ``_generate_design_storm_tool`` imported above with swmm_climate tools.
# dark-MCP registration (PR 1, issue #246): 6 calibration tools registered
# as first-class typed ToolSpecs so the LLM planner can select them by name.
# The handler module uses the same lazy-import dance as swmm_runner / swmm_plot.
from agentic_swmm.agent.tool_handlers.swmm_calibration import (  # noqa: E402,F401
    _swmm_calibrate_common_schema,
    # args mappers
    _calibrate_args,
    _calibrate_dream_zs_args,
    _calibrate_search_args,
    _calibrate_sceua_args,
    _sensitivity_scan_args,
    _validate_args,
    # handler objects
    _swmm_calibrate_dream_zs_tool,
    _swmm_calibrate_search_tool,
    _swmm_calibrate_sceua_tool,
    _swmm_calibrate_tool,
    _swmm_sensitivity_scan_tool,
    _swmm_validate_tool,
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


# New-case onboarding rewire (#246 follow-up).
# apply_onboarding is an in-process handler (not MCP-routed) — it
# calls onboarding.py internals directly.
from agentic_swmm.agent.tool_handlers.swmm_onboarding import (  # noqa: E402,F401
    _apply_onboarding_tool,
)


