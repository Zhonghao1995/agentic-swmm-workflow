"""Curated project facts: the file the agent reads at startup.

``memory/facts.md`` is tracked and human-approved; the agent injects its
content under a ``<project-facts>`` fence into the system prompt. The
agent never writes it: a candidate fact from the ``record_fact`` tool
becomes a proposal (``memory/proposals/``, see ``memory.proposals``) and
lands here only when the user runs ``aiswmm memory promote <id>``. The
staging file and the ``promote-facts`` verb that preceded proposals were
retired in the memory simplification (PR 4, 2026-09-26).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FACTS_HEADER = (
    "<!-- WHEN TO PROPOSE: user-stated preference (units/style/people),\n"
    "     project convention learned across sessions, hard-won fix recipe.\n"
    "     WHEN NOT TO PROPOSE: chitchat, single-run transient state,\n"
    "     file paths, secrets, anything the user did not affirm. -->\n"
    "# Project facts (curated)\n"
)


_FACT_BLOCK_DELIMITER = "§"
_FACTS_INJECTION_TOKEN_BUDGET = 1500


@dataclass(frozen=True)
class FactsPaths:
    """Resolved paths to the curated facts files.

    Bundled into a dataclass so tests can override the lot via
    :func:`resolve_paths` without juggling environment variables in
    helper signatures.
    """

    curated_dir: Path
    facts_md: Path


def resolve_paths(repo_root: Path | None = None) -> FactsPaths:
    """Return the resolved facts paths.

    Honours ``AISWMM_FACTS_DIR`` for tests; otherwise the facts live in the
    workspace's ``memory/`` folder next to the store (``memory/facts.md``,
    promoted and human-approved). ``repo_root`` overrides the workspace root.
    """
    override = os.environ.get("AISWMM_FACTS_DIR")
    if override:
        curated_dir = Path(override)
    elif repo_root is not None:
        curated_dir = repo_root / "memory"
    else:
        from agentic_swmm.utils.paths import resolve_memory_dir

        curated_dir = resolve_memory_dir().parent
    return FactsPaths(
        curated_dir=curated_dir,
        facts_md=curated_dir / "facts.md",
    )


def ensure_facts_md_exists(paths: FactsPaths) -> None:
    """Create ``facts.md`` with the canonical header if it's missing.

    Idempotent — already-existing files are not touched.
    """
    paths.curated_dir.mkdir(parents=True, exist_ok=True)
    if not paths.facts_md.exists():
        paths.facts_md.write_text(FACTS_HEADER, encoding="utf-8")


def read_facts_for_injection(
    paths: FactsPaths | None = None,
    *,
    max_tokens: int = _FACTS_INJECTION_TOKEN_BUDGET,
) -> str:
    """Return a ``<project-facts>``-fenced block for system-prompt injection.

    Reads ``facts.md`` only. Returns the
    empty string when the file is missing, empty, or contains nothing
    but the header (so the planner doesn't pay the fence cost for an
    empty project).
    """
    if paths is None:
        paths = resolve_paths()
    if not paths.facts_md.exists():
        return ""
    raw = paths.facts_md.read_text(encoding="utf-8", errors="ignore").strip()
    body = _strip_comment_header(raw)
    if not body.strip() or body.strip() == "# Project facts (curated)":
        return ""
    truncated = _truncate_to_token_budget(body, max_tokens)
    return (
        '<project-facts source="curated">\n'
        "<!-- Curated by the user; treat as durable project context. -->\n"
        f"{truncated}\n"
        "</project-facts>"
    )


def _strip_comment_header(text: str) -> str:
    """Drop the leading HTML comment header from ``facts.md`` content.

    The comment is part of the file template and is not useful in the
    injection (the planner already knows what curated facts are).
    """
    if text.startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            return text[end + 3 :].lstrip("\n")
    return text


def _truncate_to_token_budget(text: str, budget: int) -> str:
    """Cheap word/4 heuristic — same shape used elsewhere in this package."""
    if not text or budget <= 0:
        return text
    words = text.split()
    est_tokens = max(1, len(words))
    if est_tokens <= budget:
        return text
    truncated = " ".join(words[: max(1, budget)])
    return truncated + "\n...(truncated)..."


def fence_pattern() -> re.Pattern[str]:
    """Compiled regex matching the ``<project-facts>`` fence block."""
    return _PROJECT_FACTS_FENCE


_PROJECT_FACTS_FENCE = re.compile(
    r"<project-facts\b[^>]*>.*?</project-facts>",
    flags=re.DOTALL | re.IGNORECASE,
)


def iter_promoted_blocks(text: str) -> Iterable[str]:
    """Yield individual fact blocks from a facts.md / staging text."""
    pattern = re.compile(
        rf"{re.escape(_FACT_BLOCK_DELIMITER)}\n(.*?)\n{re.escape(_FACT_BLOCK_DELIMITER)}",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(text):
        yield match.group(1)
