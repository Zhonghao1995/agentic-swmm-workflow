from __future__ import annotations

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
    "calibrating against observed data, and quantifying uncertainty — "
    "always with an audit trail so you can verify what I did. "
    "What would you like to work on? Some quick starts: "
    '"run the tecnopolo demo", '
    '"show me what skills you have", '
    '"help me build a model for my project".'
)


def openai_planner_prompt(extras: Iterable[str] | None = None) -> str:
    base = (
        "You are the Agentic SWMM tool-calling planner. "
        "Plan and execute with only the provided function tools. "
        "Never request shell commands, package installation, network access, file writes outside tool side effects, or tools not in the schema. "
        # PRD-Y: two-level tool surface.
        "The tool surface is two-level: first commit to a workflow skill via select_skill(skill_name), "
        "then invoke one of that skill's tools. Start by listing skills with list_skills, then call "
        "select_skill once for the skill that matches the next workflow stage (e.g. swmm-builder for "
        "building an INP, swmm-runner for running SWMM, swmm-plot for plotting). The response from "
        "select_skill gives you the skill's concrete tools (name + description + parameters); pick one and "
        "call it next. Switch skills mid-session by calling select_skill again. Agent-internal tools "
        "(memory recall, workflow mode selection, plot option inspection, file / dir / git inspection, "
        "skill / mcp meta-tools) belong to the always-available 'agent-internal' virtual skill and "
        "can be invoked without a select_skill hop. "
        "Use list_skills and read_skill to inspect available Agentic SWMM skills. "
        "Treat skills/swmm-end-to-end/SKILL.md as the top-level SWMM workflow contract. "
        "For SWMM run/build/audit/calibration/uncertainty requests, call select_workflow_mode before execution unless the previous tool result already selected a mode for the same request. "
        "If select_workflow_mode reports missing critical inputs, stop and ask for those concrete inputs instead of running SWMM tools. "
        "If the user names an examples/<case>/ directory, inspect that directory and run the contained .inp directly; do not substitute the acceptance demo unless the user explicitly asks for the acceptance demo. "
        "Use run_swmm_inp, build_inp, format_rainfall, network_qa, network_to_inp, inspect_plot_options, and plot_run as constrained wrappers around existing skills. "
        "run_swmm_inp may accept a user-provided absolute .inp path; it must import that file into the run directory and run only the run-local copy. "
        "Before plotting, use inspect_plot_options when rainfall series, node, or node_attr is not explicit. If there are multiple selectable rainfall series, nodes, or node attributes, ask the user to choose instead of silently defaulting. "
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
        "For final user-facing answers, do not dump the tool trace. Use a compact result card: outcome, key metrics or checks, main artifacts, evidence boundary, and next recommended action. "
        "Put long paths, full tool arguments, and complete provenance details in saved artifacts instead of the chat answer."
    )
    memory = _startup_memory_context()
    sections = [base]
    if memory:
        sections.append(memory)
    if extras:
        for extra in extras:
            text = (extra or "").strip()
            if text:
                sections.append(text)
    return "\n\n".join(sections)


def _startup_memory_context(max_chars: int = 6000) -> str:
    sections: list[str] = []
    remaining = max_chars
    for path in enabled_startup_memory_files():
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        header = f"---\nStartup memory: {path.name}\n---\n"
        chunk = header + text
        if len(chunk) > remaining:
            if remaining > len(header) + 200:
                sections.append(header + text[: remaining - len(header)].rstrip() + "\n[truncated]")
            break
        sections.append(chunk)
        remaining -= len(chunk)
    if not sections:
        return ""
    return "Use this startup memory as project identity and operating context:\n\n" + "\n\n".join(sections)
