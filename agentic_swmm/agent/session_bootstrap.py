"""Interactive-session filesystem bootstrap (PRD-02).

Before PRD-02 these helpers were private functions on
``runtime_loop.py`` (``_safe_name`` / ``_display_path`` /
``_new_interactive_session`` / ``_case_slug`` /
``_match_registered_case``). They share a single concern — preparing
the per-session filesystem location and naming the case derived
from the user's prompt — so collecting them in one module makes
the REPL caller dramatically shallower.

The functions are kept side-effect-thin (only ``new_interactive_session``
mkdir's and writes an index line); everything else is pure.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_swmm.agent.ui import display_path

__all__ = [
    "display_path",
    "infer_case_slug",
    "new_interactive_session",
    "safe_name",
]


def new_interactive_session(base_dir: Path) -> tuple[Path, str]:
    """Create today's run-folder under ``base_dir`` and return ``(dir, label)``.

    Side effects:

    - mkdir ``base_dir/YYYY-MM-DD`` (idempotent),
    - append a ``session_start`` record to ``_sessions.jsonl`` so the
      living-memory MOC has a turn-zero anchor.

    The session label is ``session-HHMMSS`` (UTC-naive, local clock).
    """
    now = datetime.now()
    date_dir = base_dir / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    session_label = f"session-{now.strftime('%H%M%S')}"
    _append_session_index(
        date_dir,
        {
            "event": "session_start",
            "session": session_label,
            "created_at": now.isoformat(timespec="seconds"),
        },
    )
    return date_dir, session_label


def _append_session_index(date_dir: Path, event: dict[str, Any]) -> None:
    index = date_dir / "_sessions.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def safe_name(value: str) -> str:
    """Normalise an arbitrary string into a filesystem-safe slug.

    Mirrors ``agentic_swmm.agent.single_shot._safe_name``: non-alphanumeric
    runs collapse to ``-``, leading/trailing dashes strip, and an
    empty result falls back to ``"agent"`` so callers can rely on a
    non-empty filename fragment.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"


def infer_case_slug(prompt: str) -> str:
    """Derive the case slug for a per-turn run folder from ``prompt``.

    Resolution order (preserved from the previous
    ``runtime_loop._case_slug``):

    1. ``examples/<name>/...`` path mention → ``safe_name(<name>)[:32]``,
    2. ``<name>.inp`` mention → ``safe_name(<name>)[:32]``,
    3. PRD #118 case registry hit (``case_id`` / ``display_name``
       / ``aliases``),
    4. plot vocab in the prompt → ``"plot-selection"``,
    5. fallback: ``safe_name(prompt)[:32]``.
    """
    lowered = prompt.lower()
    # Note: the character class below includes CJK full-width
    # punctuation (``，。；``) so prompts mixing English filenames
    # with Chinese sentence boundaries still capture the filename
    # cleanly. Kept identical to the previous ``runtime_loop._case_slug``.
    example = re.search(r"examples/([^/\s，。；;,)]+)", prompt, flags=re.I)
    if example:
        return safe_name(example.group(1))[:32]
    inp = re.search(r"([^/\s，。；;,)]+)\.inp", prompt, flags=re.I)
    if inp:
        return safe_name(inp.group(1))[:32]
    registry_hit = _match_registered_case(lowered)
    if registry_hit is not None:
        return registry_hit
    if any(word in lowered for word in ("plot", "作图", "画图", "图")):
        return "plot-selection"
    return safe_name(prompt)[:32]


def _match_registered_case(lowered_prompt: str) -> str | None:
    """Return the first registered case id whose handle appears in the prompt.

    PRD #118 — the registry is read from ``cases/<id>/case_meta.yaml``
    under ``repo_root()``. Failures are swallowed: a corrupt registry
    must never block a user's turn.
    """
    from agentic_swmm.case import case_registry  # local: registry pulls yaml

    try:
        cases = case_registry.list_cases()
    except Exception:  # pragma: no cover - defensive
        return None
    for meta in cases:
        needles: list[str] = [meta.case_id]
        if meta.display_name:
            needles.append(meta.display_name)
        aliases = meta.extra.get("aliases") if isinstance(meta.extra, dict) else None
        if isinstance(aliases, list):
            needles.extend(str(a) for a in aliases if isinstance(a, str))
        for needle in needles:
            if needle and needle.lower() in lowered_prompt:
                return meta.case_id
    return None
