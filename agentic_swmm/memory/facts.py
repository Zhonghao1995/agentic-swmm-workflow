"""LLM-curated project facts: staging append + manual promotion.

Two files live side by side:

- ``agent/memory/curated/facts.md``
    Tracked. The agent reads this at startup and injects its content
    under a ``<project-facts>`` fence into the system prompt.
- ``agent/memory/curated/facts_staging.md``
    Gitignored. The ``record_fact`` tool appends candidate facts here.
    The user reviews and promotes them with ``aiswmm memory promote-facts``.

The staging file is never injected — only ``facts.md``. This keeps
unreviewed LLM-proposed text out of the live system prompt.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
    staging_md: Path


def resolve_paths(repo_root: Path | None = None) -> FactsPaths:
    """Return the resolved facts paths.

    Honours ``AISWMM_FACTS_DIR`` for tests; otherwise resolves under
    ``agent/memory/curated/`` of the supplied (or package) repo root.
    """
    override = os.environ.get("AISWMM_FACTS_DIR")
    if override:
        curated_dir = Path(override)
    else:
        if repo_root is None:
            from agentic_swmm.utils.paths import repo_root as _repo_root

            repo_root = _repo_root()
        curated_dir = repo_root / "agent" / "memory" / "curated"
    return FactsPaths(
        curated_dir=curated_dir,
        facts_md=curated_dir / "facts.md",
        staging_md=curated_dir / "facts_staging.md",
    )


def ensure_facts_md_exists(paths: FactsPaths) -> None:
    """Create ``facts.md`` with the canonical header if it's missing.

    Idempotent — already-existing files are not touched.
    """
    paths.curated_dir.mkdir(parents=True, exist_ok=True)
    if not paths.facts_md.exists():
        paths.facts_md.write_text(FACTS_HEADER, encoding="utf-8")


def record_fact_to_staging(
    text: str,
    *,
    source_session_id: str | None = None,
    paths: FactsPaths | None = None,
    now: datetime | None = None,
) -> Path:
    """Append a candidate fact block to the staging file.

    The block uses ``§`` as both opener and closer; the same delimiter
    is what ``aiswmm memory promote-facts`` uses to count what got
    promoted. Returns the staging file path so callers can include it
    in tool summaries.
    """
    if paths is None:
        paths = resolve_paths()
    text = (text or "").strip()
    if not text:
        raise ValueError("record_fact_to_staging: text must not be empty")
    paths.curated_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    block = (
        f"{_FACT_BLOCK_DELIMITER}\n"
        f"text: {text}\n"
        f"source_session: {source_session_id or 'unknown'}\n"
        f"proposed_utc: {timestamp}\n"
        f"{_FACT_BLOCK_DELIMITER}\n"
    )
    leading = ""
    if paths.staging_md.exists() and paths.staging_md.stat().st_size > 0:
        existing = paths.staging_md.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            leading = "\n"
    with paths.staging_md.open("a", encoding="utf-8") as handle:
        handle.write(leading + block)
    return paths.staging_md


def read_facts_for_injection(
    paths: FactsPaths | None = None,
    *,
    max_tokens: int = _FACTS_INJECTION_TOKEN_BUDGET,
) -> str:
    """Return a ``<project-facts>``-fenced block for system-prompt injection.

    Reads ``facts.md`` only — staging is never injected. Returns the
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


def promote_facts(
    *,
    paths: FactsPaths | None = None,
    editor: str | None = None,
) -> dict:
    """Open the staging file in ``$EDITOR``, then append to ``facts.md``.

    Returns ``{"ok": bool, "promoted_blocks": int, ...}`` so the CLI
    layer can render a summary. If the editor exits non-zero, neither
    file is modified.
    """
    if paths is None:
        paths = resolve_paths()
    ensure_facts_md_exists(paths)
    if not paths.staging_md.exists() or paths.staging_md.stat().st_size == 0:
        return {
            "ok": True,
            "promoted_blocks": 0,
            "reason": "staging is empty",
            "facts_md": str(paths.facts_md),
            "staging_md": str(paths.staging_md),
        }

    editor = editor or os.environ.get("EDITOR") or "vi"
    try:
        # The editor receives the staging file as its argv. Tests pass
        # in ``true`` (no-op) or ``false`` (abort) via $EDITOR.
        cmd = editor.split() + [str(paths.staging_md)]
        proc = subprocess.run(cmd, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "promoted_blocks": 0,
            "reason": f"editor invocation failed: {exc}",
            "facts_md": str(paths.facts_md),
            "staging_md": str(paths.staging_md),
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "promoted_blocks": 0,
            "reason": f"editor exited with rc={proc.returncode}; staging left unchanged",
            "facts_md": str(paths.facts_md),
            "staging_md": str(paths.staging_md),
        }

    staging_body = paths.staging_md.read_text(encoding="utf-8")
    block_count = _count_blocks(staging_body)
    if not staging_body.strip():
        paths.staging_md.write_text("", encoding="utf-8")
        return {
            "ok": True,
            "promoted_blocks": 0,
            "reason": "staging emptied by editor; nothing to promote",
            "facts_md": str(paths.facts_md),
            "staging_md": str(paths.staging_md),
        }

    facts_existing = paths.facts_md.read_text(encoding="utf-8")
    leading = "" if facts_existing.endswith("\n") else "\n"
    with paths.facts_md.open("a", encoding="utf-8") as handle:
        handle.write(leading + staging_body if not staging_body.startswith("\n") else leading + staging_body)
        if not staging_body.endswith("\n"):
            handle.write("\n")
    paths.staging_md.write_text("", encoding="utf-8")
    return {
        "ok": True,
        "promoted_blocks": block_count,
        "reason": f"promoted {block_count} entr{'y' if block_count == 1 else 'ies'} to facts.md",
        "facts_md": str(paths.facts_md),
        "staging_md": str(paths.staging_md),
    }


def _count_blocks(text: str) -> int:
    delimiters = re.findall(rf"^{re.escape(_FACT_BLOCK_DELIMITER)}\s*$", text, flags=re.M)
    return max(0, len(delimiters) // 2)


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
