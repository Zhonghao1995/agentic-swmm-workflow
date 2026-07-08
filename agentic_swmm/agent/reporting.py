"""``final_report.md`` writer.

PRD_runtime "Module: Report template" rewrites this file. The new
layout has two narrative sections (``## What I did``, ``## What you
got``) and a footer reference to ``agent_trace.jsonl``. The inline
``allowed_tools`` comma-list is dropped; the planner's ``final_text``
still renders verbatim for the user's reading.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent import tui_chrome as _chrome
from agentic_swmm.agent.swmm_runtime import run_layout


_ARTIFACT_KIND_LABELS = {
    "input": "Inputs",
    "run": "Run output",
    "plot": "Plots",
    "audit": "Audit",
    "other": "Other artifacts",
}

# ADR-0004: path-fragment classifiers built from run_layout's canonical
# stage name + its legacy aliases, so this module never hardcodes a
# stage number that could drift out of sync with the single source of
# truth. ``run_layout.PLOT`` is ``08_plot``; its legacy generation is
# ``07_plots``. ``run_layout.AUDIT`` is already ``09_audit`` (unchanged
# by ADR-0004) with ``06_audit`` as an older legacy alias.
_PLOT_DIR_FRAGMENTS = tuple(
    f"/{name}/" for name in (run_layout.PLOT, *run_layout.LEGACY_ALIASES[run_layout.PLOT])
)
_AUDIT_DIR_FRAGMENTS = tuple(
    f"/{name}" for name in (run_layout.AUDIT, *run_layout.LEGACY_ALIASES[run_layout.AUDIT])
)

# Tools whose result paths are LLM *input* (skill contracts they read,
# directory listings, registry lookups), not produced artifacts. The
# legacy ``_what_you_got`` filtered nothing and the section ended up
# dominated by SKILL.md paths from ``read_skill`` / ``select_skill``
# while real artifacts (synth.inp, model.rpt, audit JSONs, PNGs) were
# missed because their paths sit under ``result["results"]`` /
# ``result["excerpt"]`` / ``result["args"]`` rather than the top-level
# ``result["path"]``. See ``test_reporting_what_you_got_artifacts.py``.
_INTROSPECTION_TOOLS = frozenset(
    {
        "capabilities",
        "list_dir",
        "list_mcp_servers",
        "list_mcp_tools",
        "list_skills",
        "read_file",
        "read_skill",
        "search_files",
        "select_skill",
    }
)

# Path suffixes that look like real modeling artifacts. Used by the
# recursive path-miner to filter out random strings that happen to
# contain a slash (timestamps, URLs, etc.). Mirror the suffixes the
# kind-classifier already keys off so a path that gets collected can
# also be classified.
_ARTIFACT_SUFFIXES = (
    ".inp",
    ".rpt",
    ".out",
    ".png",
    ".pdf",
    ".json",
    ".md",
    ".csv",
    ".dat",
    ".geoparquet",
    ".parquet",
    ".tif",
    ".txt",
)

# Path fragments that mark planner bookkeeping (not user-facing
# artifacts). ``tool_results/`` is where ``_run_cli_tool`` /
# ``_run_process_tool`` mirror stdout / stderr. The session-level
# trace/state files are the runtime's own bookkeeping, not modeling
# output. The ``stdout.txt`` / ``stderr.txt`` siblings of model.rpt
# inside ``20_swmm_run/`` are also noise from the SWMM CLI wrapper.
_PLANNER_INTERNAL_FRAGMENTS = (
    "/tool_results/",
    "/agent_trace.jsonl",
    "/memory_trace.jsonl",
    "/session_state.json",
    "/context_summary.md",
    "/final_report.md",
    "/stdout.txt",
    "/stderr.txt",
)

# Path fragments that mark LLM reading material (skills, docs, repo
# code). These can show up in legitimate result payloads (e.g.
# ``read_skill`` putting a SKILL.md at ``result["path"]``) but they
# are LLM *inputs*, never produced artifacts.
_LLM_INPUT_FRAGMENTS = ("/skills/", "/docs/")


def write_report(
    session_dir: Path,
    goal: str,
    plan: list[Any],
    results: list[dict[str, Any]],
    *,
    dry_run: bool,
    allowed_tools: set[str],
    planner: str = "rule",
    final_text: str = "",
) -> Path:
    report_path = session_dir / "final_report.md"
    ok = all(result.get("ok") for result in results) if results else dry_run
    status = "DRY RUN" if dry_run else ("PASS" if ok else "FAIL")

    lines: list[str] = [
        "# Agentic SWMM Executor Report",
        "",
        f"- goal: {goal}",
        f"- planner: {planner}",
        f"- status: {status}",
        f"- session_dir: {session_dir}",
        "",
        "## What I did",
        "",
    ]
    did_bullets = _what_i_did(plan, results)
    if did_bullets:
        lines.extend(did_bullets)
    else:
        lines.append("- (no tool calls)")
    lines.extend(["", "## What you got", ""])
    got_lines = _what_you_got(results)
    if got_lines:
        lines.extend(got_lines)
    else:
        lines.append("- (no artifacts)")
    if final_text:
        lines.extend(["", "## Planner Final Answer", "", final_text])
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "This executor only reports commands and artifacts it actually ran or read. A successful SWMM run is not a calibration or validation claim unless observed-data evidence and validation checks are present.",
            "",
            f"_{len(allowed_tools)} tools were available; see `agent_trace.jsonl` for the full call log._",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _what_i_did(plan: list[Any], results: list[dict[str, Any]]) -> list[str]:
    """Render the planner trace as plain-English bullets."""
    bullets: list[str] = []
    for index, call in enumerate(plan, start=1):
        result = results[index - 1] if index - 1 < len(results) else None
        status = "ok" if (result and result.get("ok")) else "skipped"
        if result is None:
            status = "skipped"
        elif not result.get("ok"):
            status = "failed"
        summary = (result or {}).get("summary") or ""
        args = getattr(call, "args", {}) or {}
        args_text = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
        suffix = f" ({summary})" if summary else ""
        head = f"- {index}. `{call.name}`"
        if args_text:
            head += f" {args_text}"
        bullets.append(f"{head} — {status}{suffix}")
    return bullets


def _looks_like_artifact_path(value: str) -> bool:
    """Heuristic: is this string a path that a user would want to open?

    Must be (1) absolute, (2) end in a known artifact suffix, and (3)
    not be planner bookkeeping or LLM-input material. We do NOT touch
    the filesystem — the recursive miner runs against the in-memory
    ``results`` payload and the user can always check disk themselves
    if a path is stale.
    """
    if not value.startswith("/"):
        return False
    lowered = value.lower()
    if not lowered.endswith(_ARTIFACT_SUFFIXES):
        return False
    if any(frag in lowered for frag in _PLANNER_INTERNAL_FRAGMENTS):
        return False
    if any(frag in lowered for frag in _LLM_INPUT_FRAGMENTS):
        return False
    return True


def _mine_paths(value: Any, sink: list[str]) -> None:
    """Recursively walk a result payload and collect artifact paths.

    Handlers vary wildly in where they stash paths:

    * ``_run_cli_tool`` returns ``{"args": {"out_png": "/p"}, ...}``
    * ``_synth_swmm_from_bbox_tool`` returns
      ``{"results": {"inp_path": "/p", "raw_manifest_path": "/q"}}``
    * MCP-routed handlers return ``{"excerpt": "<json string>"}`` or
      ``{"results": {"content": [{"text": "<json string>"}]}}``
    * Some handlers also dump a ``"summary"`` like
      ``"map: /path/to.png"`` or ``"synth_inp=/path"``

    Walking the payload recursively (instead of teaching this module
    every handler's schema) keeps the miner robust to future handlers
    being added without updating the report renderer.
    """
    if isinstance(value, str):
        # Direct string path.
        if _looks_like_artifact_path(value):
            sink.append(value)
            return
        # JSON strings emitted by MCP tools — the ``excerpt`` /
        # ``content[].text`` carry the real manifest. Cheap to parse
        # speculatively; on failure we fall back to substring mining.
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                _mine_paths(json.loads(stripped), sink)
                return
            except (ValueError, TypeError):
                pass
        # ``summary`` strings like ``map: /path/x.png`` or
        # ``synth_inp=/path/y.inp`` — split on whitespace / common
        # separators and probe each token.
        for token in value.replace("=", " ").replace(":", " ").split():
            if _looks_like_artifact_path(token):
                sink.append(token)
        return
    if isinstance(value, dict):
        for v in value.values():
            _mine_paths(v, sink)
        return
    if isinstance(value, list):
        for v in value:
            _mine_paths(v, sink)


def _what_you_got(results: list[dict[str, Any]]) -> list[str]:
    """Group artifact paths by kind for the reader.

    Skips introspection tools (``read_skill`` / ``list_*`` / ...) so
    their LLM-input paths don't drown out real artifacts. Recursively
    mines paths from the result payload — handlers can stash paths
    anywhere (top-level ``path``, nested ``results.*_path``, embedded
    in an MCP ``excerpt`` JSON string, in the ``summary`` text, ...)
    and the renderer surfaces them all. See
    ``tests/test_reporting_what_you_got_artifacts.py`` for the lock-in
    against the introspection-paths-drown-real-artifacts regression.
    """
    grouped: dict[str, list[str]] = {}
    seen: set[str] = set()
    for result in results:
        tool = str(result.get("tool", "")).lower()
        if tool in _INTROSPECTION_TOOLS:
            continue
        collected: list[str] = []
        _mine_paths(result, collected)
        for path in collected:
            if path in seen:
                continue
            seen.add(path)
            kind = _classify_artifact(result, path)
            grouped.setdefault(kind, []).append(path)
    if not grouped:
        return []
    lines: list[str] = []
    for kind in ("input", "run", "plot", "audit", "other"):
        if kind not in grouped:
            continue
        lines.append(f"- **{_ARTIFACT_KIND_LABELS[kind]}**")
        for item in grouped[kind]:
            lines.append(f"    - `{item}`")
    return lines


def _classify_artifact(result: dict[str, Any], path: str) -> str:
    tool = str(result.get("tool", "")).lower()
    path_lower = str(path).lower()
    if (
        tool == "plot_run"
        or path_lower.endswith(".png")
        or any(frag in path_lower for frag in _PLOT_DIR_FRAGMENTS)
    ):
        return "plot"
    if (
        tool == "audit_run"
        or any(frag in path_lower for frag in _AUDIT_DIR_FRAGMENTS)
        or "audit_note" in path_lower
    ):
        return "audit"
    if tool == "run_swmm_inp" or path_lower.endswith((".out", ".rpt")):
        return "run"
    if path_lower.endswith((".inp", ".csv", ".json")) or tool in {"read_file", "format_rainfall", "build_inp"}:
        return "input"
    return "other"


def write_event(path: Path, payload: dict[str, Any]) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Memory-informed runtime mirror events (PRD-07 §2).
# ---------------------------------------------------------------------------
#
# The runtime already writes ``memory_trace.jsonl`` for chat-time
# inspection. PRD-07 §2 specifies that the same decisions also mirror
# into the agent-level ``agent_trace.jsonl`` so a run-time audit (which
# reads the agent trace) sees the memory consultations alongside the
# tool calls. The duplication is intentional — chat-time vs run-time
# audit have different consumers.
#
# Both helpers go through ``write_event`` so the timestamp + JSON
# formatting stay identical to every other agent_trace event. They
# accept the structural fields PRD-07 specified and let the caller
# omit anything that has no meaningful value at the call site.


def write_memory_consultation(
    path: Path,
    *,
    kind: str,
    case_meta: dict[str, Any] | None,
    evidence_count: int,
    consensus_fields: list[str] | None = None,
    ambiguous_fields: list[str] | None = None,
    queried_at_utc: str | None = None,
) -> None:
    """Append one ``memory_consultation`` event to ``path``.

    Arguments mirror PRD-07 §2:

    * ``kind`` — short label identifying which decision point did the
      consult (e.g. ``"workflow_defaults"``,
      ``"planner_intent_disambiguation"``).
    * ``case_meta`` — best-effort case identifier dict. Empty/None
      collapses to ``{}`` so the JSON shape stays stable.
    * ``evidence_count`` — number of parametric hits visible to the
      decision point.
    * ``consensus_fields`` / ``ambiguous_fields`` — optional lists
      naming which fields the memory consensus covered vs. which
      remained ambiguous. Default to empty lists when omitted.
    * ``queried_at_utc`` — caller-supplied timestamp. When ``None``
      the helper does *not* invent one — ``write_event`` already
      stamps the line with ``timestamp_utc`` so consumers always
      have a wall-clock for the consult.

    The event is best-effort: an exception during the write is
    *not* swallowed here because the call sites already wrap this in
    try/except (memory must never break dispatch). Surfacing the
    exception locally makes failures easier to diagnose during
    testing.
    """
    payload: dict[str, Any] = {
        "event": "memory_consultation",
        "kind": kind,
        "case_meta": dict(case_meta or {}),
        "evidence_count": int(evidence_count),
        "consensus_fields": list(consensus_fields or []),
        "ambiguous_fields": list(ambiguous_fields or []),
    }
    if queried_at_utc:
        payload["queried_at_utc"] = queried_at_utc
    write_event(path, payload)


def write_memory_informed_decision(
    path: Path,
    *,
    field: str,
    value_chosen: Any,
    rationale: str,
    source_runs: list[str] | None = None,
) -> None:
    """Append one ``memory_informed_decision`` event to ``path``.

    The schema is the PRD-07 §2 contract:

    * ``field`` — short identifier of the decision point (e.g.
      ``"plot_node"``, ``"rain_kind"``, ``"time_step_sec"``).
    * ``value_chosen`` — the value the agent picked. Rendered into
      JSON as-is, so callers should pass scalars or json-safe types.
    * ``rationale`` — short human-readable phrase the chat note
      will surface in its Source column.
    * ``source_runs`` — optional list of ``run_id`` strings the
      decision drew on. Defaults to an empty list so the JSON
      shape is stable across call sites.
    """
    payload: dict[str, Any] = {
        "event": "memory_informed_decision",
        "field": field,
        "value_chosen": value_chosen,
        "rationale": rationale,
        "source_runs": list(source_runs or []),
    }
    write_event(path, payload)


# ---------------------------------------------------------------------------
# Retro-chrome final result card (PRD-TUI-REDESIGN).
# CONCURRENCY-OWNER: PRD-TUI-REDESIGN
# ---------------------------------------------------------------------------
#
# ``render_result_card`` produces the rounded-frame "RUN COMPLETE" card
# the user sees at the end of a workflow. The card's six fields
# (outcome / run dir / metrics / artifacts / boundary / next) reuse the
# data that ``write_report`` already collects — we wrap the existing
# structure, not invent new fields.


def _summarise_metrics(results: list[dict[str, Any]]) -> str:
    """Return a one-line summary of the run's metric output.

    Today the executor doesn't surface continuity / peak / etc.
    directly, so we fall back to "<N> tool calls, <M> succeeded".
    A future PRD can hand metrics into this card without changing the
    visual chrome.
    """
    if not results:
        return "(no metrics)"
    total = len(results)
    ok_count = sum(1 for r in results if r.get("ok"))
    return f"{ok_count}/{total} tool calls succeeded"


def _summarise_artifacts(results: list[dict[str, Any]]) -> str:
    """Return a one-line count summary of artifact paths."""
    paths = [r.get("path") for r in results if r.get("path")]
    if not paths:
        return "(none)"
    return f"{len(paths)} artifact(s)"


def render_result_card(
    *,
    outcome: str,
    run_dir: Path | str,
    metrics: str,
    artifacts: str,
    boundary: str,
    next_action: str,
) -> str:
    """Render the rounded-frame ``[SYS] RUN COMPLETE`` card.

    Plain mode collapses to ``== [SYS] RUN COMPLETE ==`` followed by
    each field on its own line; no box-drawing characters survive.

    All field values are coerced to ``str`` so callers can pass
    :class:`Path` directly for ``run_dir``.
    """
    lines = [
        f"Outcome:   {outcome}",
        f"Run dir:   {run_dir}",
        f"Metrics:   {metrics}",
        f"Artifacts: {artifacts}",
        f"Boundary:  {boundary}",
        f"Next:      {next_action}",
    ]
    return _chrome.frame(title="[SYS] RUN COMPLETE", lines=lines)


def render_result_card_from_run(
    *,
    session_dir: Path,
    results: list[dict[str, Any]],
    dry_run: bool,
) -> str:
    """Build a result card from the data ``write_report`` already has.

    Convenience wrapper that derives outcome / metrics / artifacts
    from ``results`` so the runtime loop doesn't have to repeat the
    same aggregation. ``boundary`` is fixed to the standard executor
    contract; ``next_action`` is a generic hint that the planner's
    ``final_text`` already covers in detail.
    """
    if dry_run:
        outcome = "DRY RUN"
    else:
        ok = bool(results) and all(r.get("ok") for r in results)
        outcome = "SUCCESS" if ok else "FAIL"
    return render_result_card(
        outcome=outcome,
        run_dir=session_dir,
        metrics=_summarise_metrics(results),
        artifacts=_summarise_artifacts(results),
        boundary="ran + audited, not calibrated",
        next_action="see final_report.md and agent_trace.jsonl",
    )
