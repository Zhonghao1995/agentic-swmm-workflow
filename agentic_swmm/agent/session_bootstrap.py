"""Interactive-session filesystem bootstrap (PRD-02 + Issue #205).

Before PRD-02 these helpers were private functions on
``runtime_loop.py`` (``_safe_name`` / ``_display_path`` /
``_new_interactive_session`` / ``_case_slug`` /
``_match_registered_case``). They share a single concern — preparing
the per-session filesystem location and naming the case derived
from the user's prompt — so collecting them in one module makes
the REPL caller dramatically shallower.

Issue #205 (Phase 2 of the runtime_loop split) folded four more
helpers in here as *named lifecycle phases* so the REPL planner-runner
no longer reaches into runtime_loop's private surface:

- :func:`bootstrap_session_dir` — per-turn run/chat path (was ``_new_turn_dir``).
- :func:`bootstrap_prior_state` — load prior ``aiswmm_state.json``.
- :func:`bootstrap_system_prompt` — assemble ``<facts>`` /
  ``<previous-session>`` extras.
- :func:`bootstrap_runs_root` — resolve the ``runs/`` root for MOC
  regeneration.

The functions are kept side-effect-thin (only ``new_interactive_session``
mkdir's and writes an index line); everything else is pure or
read-only.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from agentic_swmm.agent.swmm_runtime import run_layout
from typing import Any

from agentic_swmm.agent.ui import display_path

__all__ = [
    "bootstrap_prior_state",
    "bootstrap_runs_root",
    "bootstrap_session_dir",
    "bootstrap_system_prompt",
    "display_path",
    "infer_case_slug",
    "is_swmm_run_dir",
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


# --- named lifecycle phases -------------------------------------------------
#
# Each phase below replaces a tightly-coupled private helper that used to
# live on ``runtime_loop.py``. They share the bootstrap concern (preparing
# session state before the planner runs) and are now independently
# unit-testable — see ``tests/test_session_bootstrap.py``.


def bootstrap_session_dir(date_dir: Path, prompt: str, *, kind: str) -> Path:
    """Return the per-turn run/chat directory path under ``date_dir``.

    Formerly ``runtime_loop._new_turn_dir``. The path layout is::

        <date_dir>/HHMMSS_<case-slug>_<kind>[_2/3/...]

    The trailing counter is appended only when the unsuffixed path
    already exists on disk, so concurrent turns within the same second
    don't clobber each other.

    Pure path-building — the caller is responsible for ``mkdir`` (the
    REPL still does ``session_dir.mkdir(parents=True, exist_ok=True)``
    right after this returns). Keeping mkdir out lets tests assert the
    naming behaviour without touching the filesystem.
    """
    now = datetime.now()
    case = infer_case_slug(prompt)
    folder = date_dir / f"{now.strftime('%H%M%S')}_{case}_{kind}"
    counter = 2
    while folder.exists():
        folder = date_dir / f"{now.strftime('%H%M%S')}_{case}_{kind}_{counter}"
        counter += 1
    return folder


def is_swmm_run_dir(path: Path) -> bool:
    """Return True iff ``path`` looks like a finished SWMM run directory.

    A SWMM run dir is either:

    - has ``manifest.json`` *and* a runner subfolder (canonical
      ``06_runner`` per ADR-0004, or the legacy ``05_runner`` /
      ``01_runner`` names), or
    - contains both ``*.out`` and ``*.rpt`` anywhere under it.

    The REPL uses this to decide whether to pin the path as the
    "active run" so a follow-up plot / inspect call lands in the same
    folder rather than spawning a fresh turn dir.
    """
    if not path.exists() or not path.is_dir():
        return False
    if (path / "manifest.json").exists() and run_layout.find_stage(
        path, run_layout.RUNNER
    ):
        return True
    return any(path.glob("**/*.out")) and any(path.glob("**/*.rpt"))


def bootstrap_prior_state(active_run_dir: Path | None) -> dict[str, Any] | None:
    """Load ``aiswmm_state.json`` from ``active_run_dir`` if it exists.

    Formerly ``runtime_loop._load_prior_session_state``. The planner
    consumes this through ``should_introspect`` to skip re-emitting
    ``list_skills`` / ``list_mcp_*`` calls that the prior turn already
    made. Returns ``None`` when nothing is available so the planner
    falls back to its full introspection on the first turn of a fresh
    case.
    """
    if active_run_dir is None:
        return None
    state_file = active_run_dir / "aiswmm_state.json"
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def bootstrap_system_prompt(
    *,
    session_dir: Path,
    prior_session_state: dict[str, Any] | None,
) -> list[str]:
    """Assemble the per-session system-prompt injections.

    Formerly ``runtime_loop._build_system_prompt_extras``. Order:
    project facts first (durable user-curated context), then the
    previous-session banner (volatile recall). Both are gated on the
    relevant input being non-empty so the system prompt stays tight
    when there is nothing to inject.
    """
    extras: list[str] = []
    facts_block = _safe_facts_block()
    if facts_block:
        extras.append(facts_block)
    prev_block = _safe_previous_session_block(
        session_dir=session_dir,
        prior_session_state=prior_session_state,
    )
    if prev_block:
        extras.append(prev_block)
    return extras


def _safe_facts_block() -> str:
    """Read ``facts.md`` and wrap it for system-prompt injection.

    Wrapped in a try/except because a corrupt facts file should never
    block the user's turn — the worst case is a slightly less
    informed planner.
    """
    from agentic_swmm.memory import facts as _facts_mod

    try:
        return _facts_mod.read_facts_for_injection()
    except Exception:
        return ""


def _safe_previous_session_block(
    *,
    session_dir: Path,
    prior_session_state: dict[str, Any] | None,
) -> str:
    """Return a ``<previous-session>`` fence for ``session_dir``, if any.

    The lookup is keyed on ``case_name`` inferred from either the
    prior session state or the current session directory's name.
    Returns the empty string when no prior session exists or any IO
    fails — never raises in front of the user.
    """
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.case_inference import infer_case_name
    from agentic_swmm.memory.session_sync import default_db_path

    try:
        case_name: str | None = None
        if prior_session_state:
            case_name = infer_case_name(prior_session_state)
        if not case_name:
            case_name = _infer_case_name_from_dir(session_dir)
        if not case_name:
            return ""
        db_path = default_db_path()
        if not db_path.exists():
            return ""
        with session_db.connect(db_path) as conn:
            row = session_db.latest_session_for_case(conn, case_name)
        if not row:
            return ""
        current_id = session_db.session_id_from_dir(session_dir)
        if row.get("session_id") == current_id:
            return ""
        return session_db.previous_session_block(row)
    except Exception:
        return ""


def _infer_case_name_from_dir(session_dir: Path) -> str | None:
    """Derive the case slug straight from ``session_dir``'s leaf name.

    Mirrors ``case_inference.infer_case_name`` for the case where we
    only have the session directory in hand (no session_state yet).
    """
    leaf = session_dir.name
    match = re.match(r"^\d+_(?P<case>.+?)_(?:run|chat)(?:_\d+)?$", leaf)
    if match:
        return match.group("case")
    return None


def bootstrap_runs_root(session_dir: Path) -> Path:
    """Return the ``runs/`` root that the MOC should describe.

    Formerly ``runtime_loop._resolve_runs_root_for``. Order:

    1. ``AISWMM_RUNS_ROOT`` env var (lets tests point at a tmp tree).
    2. The first ancestor of ``session_dir`` named ``runs``.
    3. ``repo_root() / "runs"`` as a last-resort fallback.

    Mirrors the resolution used by ``commands/audit._runs_root_for`` so
    the session-end and force-refresh paths agree.
    """
    from agentic_swmm.utils.paths import repo_root

    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    try:
        resolved = session_dir.resolve()
    except OSError:
        resolved = session_dir
    for parent in resolved.parents:
        if parent.name == "runs":
            return parent
    return repo_root() / "runs"
