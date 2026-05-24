"""Agent introspection handlers (PRD #128 — Phase 2 Group C, FINAL group).

Family: read-only introspection of the agent runtime itself.

* ``_doctor_tool`` — runs the built-in ``aiswmm doctor`` CLI through
  the shared subprocess wrapper. Surfaces environment / config /
  optional-extras diagnostics so the planner can quote them when a
  setup is broken.
* ``_retrieve_memory_tool`` — hybrid keyword/embedding retrieval over
  the ``swmm-rag-memory`` corpus (Issue #124 Part A). Shells out to
  ``skills/swmm-rag-memory/scripts/retrieve_memory.py`` so the
  optional embedding library never enters the planner's hot path.

Both handlers are reads against agent-side state (CLI doctor output,
RAG index on disk) — they never mutate run evidence.

``_failure``, ``_run_cli_tool``, and ``_run_script_tool`` come from
``tool_handlers/_shared`` — the cross-cutting helpers every family
imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _run_cli_tool,
    _run_script_tool,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


def _doctor_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    return _run_cli_tool(call, session_dir, ["doctor"])


# -- swmm-rag-memory retrieval (Issue #124 Part A) ----------------------------
#
# The skill ships ``skills/swmm-rag-memory/scripts/retrieve_memory.py`` which
# already exposes a stable CLI; the agent invokes it via a subprocess so we
# don't pull the embedding library into the planner's hot path. Output is the
# script's JSON document (or markdown via ``--format markdown``); we return
# its trailing tail in ``stdout_tail`` so the planner sees citations.

_RAG_SKILL_DIR_RELATIVE = ("skills", "swmm-rag-memory", "scripts")


def _retrieve_memory_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Shell out to the swmm-rag-memory ``retrieve_memory.py`` script.

    Why a subprocess and not an in-process import? The retriever pulls in
    ``rag_memory_lib`` which lives under ``skills/swmm-rag-memory/scripts/``
    (not on the package import path) and optionally loads embedding vectors.
    Subprocess isolation keeps a corrupt index from poisoning the planner's
    Python state mid-turn.
    """
    query = call.args.get("query")
    if not isinstance(query, str) or not query.strip():
        return _failure(call, "missing required argument: query")
    script_path = repo_root().joinpath(*_RAG_SKILL_DIR_RELATIVE, "retrieve_memory.py")
    if not script_path.is_file():
        return _failure(call, f"retrieve_memory script not found at {script_path}")
    cli_args: list[str] = [str(script_path), "--query", query]
    top_k = call.args.get("top_k")
    if isinstance(top_k, int) and top_k > 0:
        cli_args.extend(["--top-k", str(top_k)])
    retriever = call.args.get("retriever")
    if retriever in ("keyword", "hybrid"):
        cli_args.extend(["--retriever", retriever])
    project = call.args.get("project")
    if isinstance(project, str) and project.strip():
        cli_args.extend(["--project", project])
    return _run_script_tool(call, session_dir, cli_args)


__all__ = [
    "_doctor_tool",
    "_retrieve_memory_tool",
]
