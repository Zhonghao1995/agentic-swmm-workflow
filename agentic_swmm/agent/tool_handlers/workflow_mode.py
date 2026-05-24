"""Workflow-mode selection handler (PRD #128 — Phase 2 Group C, FINAL group).

Family: top-level ``swmm-end-to-end`` operating-mode selection.

The ``select_workflow_mode`` tool is the planner's first hop: given a
free-text goal plus whatever inputs the user supplied, it picks one of
the seven adapter-registered workflow modes (calibration, uncertainty,
prepared_inp_cli, full_modular_build, existing_run_plot,
audit_only_or_comparison, prepared_demo) and reports which required
inputs are still missing. It never starts a SWMM run itself — it only
hands the planner the next-tool list and the evidence boundary.

The handler has three branches:

1. Explicit-mode short-circuit — when the LLM passes ``mode``
   directly (PRD-INTENT-OVERMATCH #95), trust it iff it's in
   ``_VALID_MODE_ENUM``. Do NOT auto-infer ``run_dir`` from global
   state on this path; that was part of the original overmatch bug.
2. Legacy keyword fallback — derive intent signals via
   ``compute_intent_signals`` (PRD #111 — single source of truth
   with the planner's auto-route disambiguator trigger). Includes
   the compound run+demo+plot pin for ``prepared_demo`` (PRD #111)
   and the bilingual Chinese-keyword goal-routing contract (#79
   regression).
3. Fallback to ``needs_user_inputs`` — sentinel prompting the user
   for either a prepared INP or the full-build input set.

``_build_response_for_mode`` is the shared payload builder used by
both the explicit-mode path and the keyword fallback.
``_workflow_user_prompt`` is the human-readable continuation prompt
the planner echoes when inputs are missing.
``_active_run_dir_from_global_state`` reads the runtime state file
so audit/plot intents can pick up the most recent run without the
user re-pasting the path.

Three helpers stay in :mod:`tool_registry` because they are shared
with handlers in other Group A/B families (``_node_suggestions``,
``_plot_selection_options_for_inp``) or are part of the registry's
public surface (``_VALID_MODE_ENUM``, ``compute_intent_signals``).
We import them lazily inside the handler so this module stays
import-cycle-free.

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.config import runtime_state_path


def _build_response_for_mode(
    call: ToolCall, mode: str, goal: str, provided: dict[str, str]
) -> dict[str, Any]:
    """Validate inputs for ``mode`` and build the tool's response payload.

    Shared between the explicit-mode short-circuit (when the LLM passed
    ``mode``) and the legacy keyword-fallback path. The ``provided`` dict
    is read-only here; auto-infer of ``run_dir`` from global state is the
    caller's responsibility and runs only in the keyword-fallback path.

    PRD-04. The per-mode ``required_inputs`` / ``recommended_next_tools``
    / ``evidence_boundary`` triples live on adapter classes in
    ``agentic_swmm.agent.workflow_modes``. The registry is the single
    source of truth; this function only adds rules that depend on the
    caller's state (the goal-text-driven baseline_run_dir addendum for
    comparison audits) or on the ``needs_user_inputs`` sentinel that
    the keyword fallback emits when no registered mode applies.
    """

    # Late imports keep the workflow_modes registry an optional
    # dependency of this module (avoiding an import cycle with the
    # planner package) and let the plot-helper seams (which stay in
    # ``tool_registry``) be monkeypatched from there per Group A/B.
    from agentic_swmm.agent.tool_registry import (
        _node_suggestions,
        _plot_selection_options_for_inp,
    )
    from agentic_swmm.agent.workflow_modes import get_mode_spec

    spec = get_mode_spec(mode)
    if spec is not None:
        required = list(spec.required_inputs)
        next_tools = list(spec.recommended_next_tools)
        boundary = spec.evidence_boundary
        # Goal-text-dependent rule: audit-comparison appends a second
        # run dir to required inputs. Lives at the call site because
        # it depends on the goal string, not on declarative spec state.
        if mode == "audit_only_or_comparison" and (
            "compare" in goal or "comparison" in goal or "比较" in goal
        ):
            required.append("baseline_run_dir")
    else:
        # ``needs_user_inputs`` is a sentinel the keyword fallback emits
        # when no registered mode applies. It is not a workflow the
        # agent dispatches; it is a prompt asking the user to provide
        # either a prepared INP or the full-build input set.
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


def _select_workflow_mode_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    # Late import so ``_VALID_MODE_ENUM`` and ``compute_intent_signals``
    # stay in ``tool_registry`` (per PRD #128 Group C scope) without an
    # import cycle at module-load time.
    from agentic_swmm.agent.tool_registry import (
        _VALID_MODE_ENUM,
        compute_intent_signals,
    )

    goal = str(call.args.get("goal") or "").lower()
    # ``mode`` is consumed below as a routing argument, not a workflow
    # input; exclude it from ``provided`` so it does not pollute the
    # echoed ``provided_inputs`` / ``provided_values`` fields.
    provided = {
        key: str(value).strip()
        for key, value in call.args.items()
        if key != "mode" and isinstance(value, str) and value.strip()
    }

    # PRD-INTENT-OVERMATCH (#95): the LLM may pass an explicit ``mode``
    # to bypass keyword-based intent re-derivation. If valid, use it
    # directly; do NOT auto-infer ``run_dir`` from global state when the
    # mode is explicit (auto-infer was part of the original bug — see
    # the PRD's Done Criteria #4).
    explicit_mode = call.args.get("mode")
    if isinstance(explicit_mode, str) and explicit_mode in _VALID_MODE_ENUM:
        return _build_response_for_mode(call, explicit_mode, goal, provided)

    # Legacy keyword-fallback path. NOTE: Chinese keywords are part of
    # the bilingual goal-routing contract. The `??` placeholders
    # previously here were a non-UTF-8 sync regression (P0-2 in #79).
    # Do not replace these with ASCII placeholders again — the
    # regression test in `tests/test_select_workflow_mode_bilingual.py`
    # will trip on any reintroduced `??` pair.
    #
    # PRD #111: signals are computed by ``compute_intent_signals`` so
    # the planner's auto-route disambiguator trigger and this keyword
    # fallback share one source of truth.
    signals = compute_intent_signals(goal)
    wants_calibration = signals["wants_calibration"]
    wants_uncertainty = signals["wants_uncertainty"]
    wants_audit = signals["wants_audit"]
    wants_plot = signals["wants_plot"]
    wants_demo = signals["wants_demo"]
    wants_run = signals["wants_run"]
    has_inp = bool(provided.get("inp_path"))
    has_run_dir = bool(provided.get("run_dir"))
    if (wants_plot or wants_audit) and not has_run_dir:
        active_run_dir = _active_run_dir_from_global_state()
        if active_run_dir:
            provided["run_dir"] = active_run_dir
            has_run_dir = True
    full_build_inputs = ["network_json", "subcatchments_csv", "rainfall_input", "landuse_input", "soil_input"]
    has_full_build = all(provided.get(key) for key in full_build_inputs)

    # PRD #111: compound run+demo+plot must pin to ``prepared_demo``
    # *before* the plot branch fires. Without this guard the plot
    # branch hijacks any goal that mentions ``plot`` once a run_dir is
    # auto-inferred from global state — which is exactly the bug from
    # ``runs/2026-05-16/120740_todcreek_run``.
    if wants_run and wants_demo and not has_inp:
        mode = "prepared_demo"
    elif wants_plot and has_run_dir:
        mode = "existing_run_plot"
    elif wants_calibration:
        mode = "calibration"
    elif wants_uncertainty:
        mode = "uncertainty"
    elif has_inp:
        mode = "prepared_inp_cli"
    elif wants_demo:
        mode = "prepared_demo"
    elif wants_audit and not has_inp:
        mode = "audit_only_or_comparison"
    elif has_full_build:
        mode = "full_modular_build"
    else:
        mode = "needs_user_inputs"

    return _build_response_for_mode(call, mode, goal, provided)


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


__all__ = [
    "_select_workflow_mode_tool",
    "_build_response_for_mode",
    "_workflow_user_prompt",
    "_active_run_dir_from_global_state",
]
