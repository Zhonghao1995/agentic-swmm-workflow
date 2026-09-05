from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from agentic_swmm.runtime.registry import enabled_startup_memory_files


# Issue #59 (UX-4): warm self-introduction template emitted on the
# *first* message of an interactive session when the prompt looks
# open-shaped (greetings, identity questions, very short / verbless
# prompts). The template intentionally stays short — three sentences
# of identity + a one-line boundary clause + three quick-start
# handles. The boundary clause ("audit trail so you can verify what I
# did") keeps the warmth honest against the rest of the agent's
# evidence-discipline vocabulary.
WARM_INTRO_TEMPLATE = (
    "Hi! I'm Agentic SWMM, your stormwater modeling collaborator. "
    "I can help with building EPA SWMM inputs, running simulations, "
    "calibrating against observed data, and quantifying uncertainty, "
    "always with an audit trail so you can verify what I did. "
    "What would you like to work on? Some quick starts: "
    '"run the tecnopolo demo", '
    '"show me what skills you have", '
    '"help me build a model for my project".'
)


_SKILL_INDEX_LINE_CAP = 160
_SKILL_INDEX_BUDGET = 4000


def _skill_description(skill_path: Path) -> str:
    """First-line description from a SKILL.md front matter (capped)."""
    try:
        text = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^description:\s*(.+)$", text, flags=re.MULTILINE)
    if not match:
        return ""
    desc = match.group(1).strip().strip('"')
    if len(desc) > _SKILL_INDEX_LINE_CAP:
        desc = desc[: _SKILL_INDEX_LINE_CAP - 1].rstrip() + "…"
    return desc


@lru_cache(maxsize=1)
def skill_index_block() -> str:
    """Inline one-line-per-skill index for the system prompt.

    ADR-0013 decision 1: every session used to open with a catalog
    prologue (list_skills -> read_skill x5-7 -> list_mcp_tools x4)
    whose payloads then compounded through full-history replay
    (measured 2026-08-09: 12 of 26 calls in a map session). Embedding
    the index once makes that reconnaissance unnecessary; the planner
    picks from the index and commits via select_skill directly.

    Cached per process: the skills tree does not change mid-session.
    A guard test pins the block under ``_SKILL_INDEX_BUDGET`` chars so
    skill descriptions cannot silently bloat the prompt.
    """
    from agentic_swmm.runtime.registry import discover_skills

    lines = ["<skill-index>"]
    for record in discover_skills():
        if not record.get("enabled", True):
            continue
        name = str(record.get("name"))
        desc = _skill_description(Path(str(record.get("path"))))
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    lines.append("</skill-index>")
    block = "\n".join(lines)
    if len(block) > _SKILL_INDEX_BUDGET:
        # Fail soft: an oversized index degrades to names only rather
        # than blowing the prompt budget.
        names = [
            f"- {r.get('name')}" for r in discover_skills() if r.get("enabled", True)
        ]
        block = "\n".join(["<skill-index>", *names, "</skill-index>"])
    return block


def openai_planner_prompt(
    extras: Iterable[str] | None = None,
    *,
    trace_path: Path | None = None,
) -> str:
    """Build the full system prompt for the OpenAI planner.

    Parameters
    ----------
    extras:
        Additional blocks to append after the base prompt and the
        startup memory section (e.g. ``<project-facts>`` and
        ``<previous-session>`` injected by :func:`bootstrap_system_prompt`).
    trace_path:
        Optional path to ``agent_trace.jsonl``.  When provided, a
        ``memory_context_budget`` event is appended recording which
        startup memory entries were excluded by the character budget.
        Pass ``None`` (the default) to skip the event (e.g. in tests).
    """
    base = (
        "You are the Agentic SWMM tool-calling planner. "
        "Plan and execute with only the provided function tools. "
        "Never request shell commands, package installation, network access, file writes outside tool side effects, or tools not in the schema. "
        # PRD-Y: two-level tool surface.
        "The tool surface is two-level: first commit to a workflow skill via select_skill(skill_name), "
        "then invoke one of that skill's tools. The <skill-index> block below lists every available "
        "skill with its purpose: pick the skill matching the next workflow stage from that index and "
        "call select_skill DIRECTLY. Do not open sessions with catalog reconnaissance (list_skills, "
        "reading multiple skills' documentation, or enumerating MCP servers/tools): the index already "
        "covers discovery, and select_skill's response gives you the chosen skill's concrete tools "
        "(name + description + parameters); pick one and call it next. read_skill remains the right "
        "move for the CHOSEN skill when its documentation contract matters to the task. Switch skills "
        "mid-session by calling select_skill again. Agent-internal tools "
        "(memory recall, plot option inspection, file / dir / git inspection, "
        "skill / mcp meta-tools) belong to the always-available 'agent-internal' virtual skill and "
        "can be invoked without a select_skill hop. "
        "Treat skills/swmm-end-to-end/SKILL.md as the top-level SWMM workflow contract. "
        "Read the SKILL.md (or the tool description) for each candidate before invoking a SWMM tool — the description plus the typed schema is the contract you commit to. "
        "If the user's request is missing a required input for the tool you want to call (e.g. an INP path, a bbox, a run directory), stop and ask for that concrete input instead of guessing or running a different tool. EXCEPTION: when the selected skill's documentation provides a VERIFIED demo default for exactly the named case (for example the swmm-canada demo AOI table for a named place like downtown Victoria), use that documented default directly and state in the result card that the demo default was used; never invent values that have no documented source. "
        "If the user names an examples/<case>/ directory, inspect that directory and run the contained .inp directly; do not substitute the acceptance demo unless the user explicitly asks for the acceptance demo. "
        "Use run_swmm_inp, build_inp, format_rainfall, network_qa, network_to_inp, inspect_plot_options, and plot_run as constrained wrappers around existing skills. "
        "For a Canadian area, fetch_swmm_from_canada returns a ready-to-run model from the SWMMCanada upstream (real municipal pipes where covered, synthesized elsewhere in Canada); pass its returned run_dir and inp_path into run_swmm_inp and then audit_run so the whole chain lands in one run folder. Outside Canada use synth_swmm_from_bbox. "
        "For climate-change what-ifs, run_climate_scenarios batches precipitation-scaled variants of a model and writes a per-scenario comparison; calibrate first (aiswmm calibrate) so the deltas are meaningful. "
        "run_swmm_inp may accept a user-provided absolute .inp path; it must import that file into the run directory and run only the run-local copy. "
        "Before plotting, use inspect_plot_options when rainfall series, node, or node_attr is not explicit. When the user's goal already asked for the plot or the report, ADOPT the recommended choices from inspect_plot_options (recommended node, Total_inflow, full window) and state in the result card which choices you used; do not stop to ask. Ask the user to choose only when the goal did not request plotting, no recommendation exists, or the session runs with the safe profile. "
        "Always plot through plot_run (never a raw MCP plot call with a hand-built output path): plot_run lands figures in the canonical plot stage of the run directory, which is where report generation looks for them. "
        "Use list_dir, search_files, read_file, and git_diff for repository workspace inspection. "
        "Use apply_patch for controlled repository edits and run_tests or run_allowed_command for allowlisted verification; never request arbitrary shell. "
        "Use web_search and web_fetch_url for source-backed web research, but keep web evidence separate from local run evidence. "
        "Use list_mcp_servers, list_mcp_tools, and call_mcp_tool when the local MCP registry exposes a better tool than the CLI wrapper; prefer CLI wrappers for core audited SWMM run/audit/plot paths unless an MCP tool has a clearer schema for the requested operation. "
        "If an MCP call fails, inspect the recovery/fallback_tools fields and retry only with corrected arguments or a listed fallback tool. "
        "Use capabilities when the user asks what this runtime can access or do. "
        "Session state and compressed context are saved under the session directory for follow-up turns and debugging. "
        "Use doctor for runtime checks, demo_acceptance for a reproducible acceptance run, audit_run for evidence capture, "
        "summarize_memory for modeling-memory refreshes, and read_file for inspecting repository artifacts. "
        "After each tool result, decide the next evidence-producing tool or stop. "
        "When a typed read tool (read_rpt_summary, inspect_plot_options, recall_memory, recall_session_history) returns the rows or values the question asks for, that result IS the evidence: answer from it and name the tool; never re-derive the same numbers from raw files with search_files or read_file. "
        "For final user-facing answers, do not dump the tool trace. Use a compact result card: outcome, key metrics or checks, main artifacts, evidence boundary, and next recommended action. "
        "Answer in the language of the user's CURRENT message (English for an English message, Chinese for a Chinese one), even when earlier sessions, memory blocks or artifacts are in another language. "
        "When a bounded analysis has sensible defaults (documented in the skill, in its examples, or in the run itself), "
        "run the first pass with those defaults and state the assumed values; ask at most ONE question, and only when "
        "a choice would change the result materially. Never answer a request with a questionnaire. "
        "When the user asks what you remember or what lessons apply, answer from the memory blocks in this prompt "
        "(<parametric-memory>, <recent-failures>, <previous-session>) and cite the runs they name; call tools only to "
        "verify a fact those blocks do not contain. "
        "When the user asks about a run that produced no results (a failed fetch, a run that never happened), say so; "
        "never present another run's results as that run. "
        "When a request names no measurable target (\"make it better\", \"improve it\"), the first line of the answer states what it was taken to mean, or the answer asks the one question. "
        "A claim that the source data's units differ from the model's declared units is a question to the user, asked once; it is never an edit or a conversion of a fetched model. "
        "The tool descriptions are the contract: never run `--help` or `help` through run_allowed_command to learn a verb, and never redo through the CLI what a typed tool has just done. "
        "A question about what happened in this session is answered from the session record (recall_session_history, the chat note, the turns so far), naming every run, every refusal and every guard that refused the assistant's own attempts, not from the run in hand alone. "
        "When request_expert_review returns approved=true, say the reviewer approved the result for decision use, "
        "with the decision id and the provenance file; when approved=false, say it was denied; "
        "a recorded decision is never pending. "
        "Put long paths, full tool arguments, and complete provenance details in saved artifacts instead of the chat answer."
    )
    memory = _startup_memory_context(trace_path=trace_path)
    sections = [base, skill_index_block()]
    if memory:
        sections.append(memory)
    if extras:
        for extra in extras:
            text = (extra or "").strip()
            if text:
                sections.append(text)
    return "\n\n".join(sections)


def _startup_memory_context(
    *,
    trace_path: Path | None = None,
) -> str:
    """Load startup memory files and apply the context character budget.

    The character budget is read from the user config
    (``memory.context_budget_chars``, default 4,000).  Files that do
    not fit within the budget are excluded from the injected block; a
    one-line availability note is appended so the model knows on-demand
    recall tools can fetch the rest.

    A ``memory_context_budget`` event is written to ``trace_path``
    (``agent_trace.jsonl``) when ``trace_path`` is not ``None`` and at
    least one file was processed.
    """
    from agentic_swmm.config import load_config
    from agentic_swmm.memory.context_budget import (
        DEFAULT_CONTEXT_BUDGET_CHARS,
        MemoryEntry,
        apply_context_budget,
        emit_budget_trace_event,
    )

    cfg = load_config()
    raw_budget = cfg.get("memory.context_budget_chars", DEFAULT_CONTEXT_BUDGET_CHARS)
    try:
        budget = int(raw_budget)
    except (TypeError, ValueError):
        budget = DEFAULT_CONTEXT_BUDGET_CHARS

    entries: list[MemoryEntry] = []
    for path in enabled_startup_memory_files():
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        header = f"---\nStartup memory: {path.name}\n---\n"
        chunk = header + text
        entries.append(MemoryEntry(id=f"startup/{path.name}", text=chunk))

    if not entries:
        return ""

    result = apply_context_budget(entries, budget=budget)

    if trace_path is not None:
        try:
            emit_budget_trace_event(trace_path, result, budget_chars=budget)
        except Exception:
            pass  # best-effort: trace failure must not block the prompt

    if not result.injected_text.strip():
        return ""
    return (
        "Use this startup memory as project identity and operating context:\n\n"
        + result.injected_text
    )
