"""Interactive shell and OpenAI planner turn loop for ``aiswmm``.

This module owns the long-lived interactive ``aiswmm`` shell as well as
the single-turn OpenAI planner driver used by both interactive and
non-interactive flows. The audit PRD's chat-only-turn hook
(``_write_chat_note_for_session``) lives here unchanged: chat-style
turns persist a ``session_state.json`` skeleton and an Obsidian-ready
``chat_note.md`` next to the agent trace.

This file was extracted from ``agentic_swmm/commands/agent.py`` in the
first commit of the Runtime UX PRD; the move was a pure split with no
behaviour change.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import sys as _sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent import tui_chrome as _chrome
from agentic_swmm.agent import ui_colors
from agentic_swmm.agent import welcome as _welcome
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.mcp_pool import ensure_session_pool
from agentic_swmm.agent.planner import _looks_like_swmm_request
from agentic_swmm.agent.prompts import WARM_INTRO_TEMPLATE
from agentic_swmm.agent.reporting import write_event as _write_event
from agentic_swmm.agent.reporting import write_report as _write_report
from agentic_swmm.agent.runtime import run_openai_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.ui import agent_say as _agent_say
from agentic_swmm.agent.ui import display_path as _display_path
from agentic_swmm.audit.chat_note import build_chat_note
from agentic_swmm.audit.moc_generator import generate_moc
from agentic_swmm.config import load_config
from agentic_swmm.memory import facts as _facts_mod
from agentic_swmm.memory import session_db
from agentic_swmm.memory.case_inference import infer_case_name
from agentic_swmm.memory.session_sync import default_db_path, sync_session_to_db
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.utils.paths import repo_root

_log = logging.getLogger(__name__)

# `_safe_name` is shared with the non-interactive path; both files need it.
from agentic_swmm.agent.single_shot import _safe_name


def run_interactive_shell(args: argparse.Namespace) -> int:
    if args.planner != "openai":
        raise ValueError("interactive agent shell currently requires `--planner openai`.")

    base_dir = args.session_dir.expanduser().resolve() if args.session_dir else repo_root() / "runs"
    base_dir.mkdir(parents=True, exist_ok=True)

    date_dir, session_label = _new_interactive_session(base_dir)

    # Late import keeps the agent runtime free of a CLI-layer dependency
    # in the import graph (commands/agent.py imports runtime_loop).
    from agentic_swmm.commands.agent import resolve_profile_string

    profile_name = resolve_profile_string(args)

    # Issue #57 (UX-2): print the logo + first-run welcome (or the
    # compact returning-user banner) before the existing one-line
    # startup banner. The welcome module owns its own NO_COLOR /
    # AISWMM_DISABLE_WELCOME / first-run-marker handling, so the
    # call here is a single line and any failure inside the welcome
    # is swallowed (decoration must not block the agent from booting).
    _welcome.print_welcome(
        session_label=session_label,
        profile_name=profile_name,
    )

    # PRD_runtime user story 6: one-line startup banner.
    # The ``profile=`` segment was added when QUICK became the default
    # (see ``format_startup_banner`` for the rendering contract).
    _agent_say(
        format_startup_banner(
            session_label=session_label,
            date_dir_display=_display_path(date_dir),
            profile_name=profile_name,
        )
    )

    turn = 0
    active_run_dir: Path | None = None
    while True:
        try:
            prompt = input("you> ").strip()
        except EOFError:
            print()
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if prompt in {"/new-session", "/new session", "new session"}:
            date_dir, session_label = _new_interactive_session(base_dir)
            active_run_dir = None
            turn = 0
            _agent_say(f"New session: {session_label}")
            _agent_say(f"Date folder: {_display_path(date_dir)}\n")
            continue
        if not prompt:
            continue

        turn += 1

        # Issue #59 (UX-4): on the *first* user prompt of a session,
        # detect open-shaped requests (greetings, identity questions,
        # short/verbless) and emit the warm intro before any tool
        # call. Task-shaped first prompts (run/build/calibrate/...)
        # fall through to the normal planner dispatch.
        intro_text = maybe_warm_intro(prompt, turn=turn)
        if intro_text is not None:
            print()
            _agent_say(intro_text)
            print()
            # Hold the planner so the user can answer the "what would
            # you like to work on?" line. ``turn`` is NOT reset here:
            # ``maybe_warm_intro`` already enforces "fires only on
            # turn=1" via its own guard, so leaving the counter to
            # advance naturally lets the second open-shaped prompt
            # fall through to the planner instead of re-firing the
            # canned template every greeting (regression for the
            # ``turn = 0`` reset that previously lived here).
            continue

        use_active_run = active_run_dir is not None and _looks_like_run_continuation(prompt)
        goal = prompt
        is_chat_turn = False
        if use_active_run:
            session_dir = active_run_dir
            goal = f"{prompt}\n\nPrevious run directory: {active_run_dir}"
        elif _looks_like_swmm_request(prompt):
            session_dir = _new_turn_dir(date_dir, prompt, kind="run")
        else:
            session_dir = _new_turn_dir(date_dir, prompt, kind="chat")
            is_chat_turn = True
        session_dir.mkdir(parents=True, exist_ok=True)
        trace_path = session_dir / "agent_trace.jsonl"
        prior_state = _load_prior_session_state(active_run_dir)
        print()
        result = run_openai_planner(
            args,
            goal,
            session_dir,
            trace_path,
            AgentToolRegistry(),
            chat_session=is_chat_turn,
            prior_session_state=prior_state,
        )
        if result == 0 and _is_swmm_run_dir(session_dir):
            active_run_dir = session_dir
        print()
        if result != 0:
            _agent_say(f"Turn failed with exit code {result}. You can continue or type /exit.\n")


def format_startup_banner(
    *,
    session_label: str,
    date_dir_display: str,
    profile_name: str,
) -> str:
    """Render the one-line startup banner for the interactive shell.

    Extracted from ``run_interactive_shell`` so the profile-segment
    rendering can be unit-tested without spinning up a session. The
    ``profile=`` segment is dimmed on a real tty (``ui_colors.DIM``)
    and falls back to plain text on non-tty / ``NO_COLOR`` per the
    ``colorize`` contract.
    """
    profile_segment = ui_colors.colorize(f"profile={profile_name}", ui_colors.DIM)
    return (
        f"aiswmm interactive ({session_label}, {date_dir_display}, "
        f"{profile_segment}) — /exit, /new-session"
    )


def _new_interactive_session(base_dir: Path) -> tuple[Path, str]:
    now = datetime.now()
    date_dir = base_dir / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    session_label = f"session-{now.strftime('%H%M%S')}"
    _append_session_index(date_dir, {"event": "session_start", "session": session_label, "created_at": now.isoformat(timespec="seconds")})
    return date_dir, session_label


def _new_turn_dir(date_dir: Path, prompt: str, *, kind: str) -> Path:
    now = datetime.now()
    case = _case_slug(prompt)
    folder = date_dir / f"{now.strftime('%H%M%S')}_{case}_{kind}"
    counter = 2
    while folder.exists():
        folder = date_dir / f"{now.strftime('%H%M%S')}_{case}_{kind}_{counter}"
        counter += 1
    return folder


def _case_slug(prompt: str) -> str:
    lowered = prompt.lower()
    example = re.search(r"examples/([^/\s，。；;,)]+)", prompt, flags=re.I)
    if example:
        return _safe_name(example.group(1))[:32]
    inp = re.search(r"([^/\s，。；;,)]+)\.inp", prompt, flags=re.I)
    if inp:
        return _safe_name(inp.group(1))[:32]
    if "tecnopolo" in lowered:
        return "tecnopolo"
    if "todcreek" in lowered or "tod creek" in lowered:
        return "todcreek"
    if any(word in lowered for word in ("plot", "作图", "画图", "图")):
        return "plot-selection"
    return _safe_name(prompt)[:32]


def _append_session_index(date_dir: Path, event: dict[str, Any]) -> None:
    index = date_dir / "_sessions.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _write_chat_note_for_session(session_dir: Path) -> Path | None:
    """Write ``chat_note.md`` for a chat-only session.

    The audit PRD (M8) requires chat sessions to carry an Obsidian-ready
    ``chat_note.md`` alongside ``session_state.json`` and
    ``agent_trace.jsonl``. We skip this for SWMM run dirs so the audit
    note remains the canonical record there.
    """
    if not session_dir.exists() or not session_dir.is_dir():
        return None
    if _is_swmm_run_dir(session_dir):
        return None

    state_path = session_dir / "session_state.json"
    trace_path = session_dir / "agent_trace.jsonl"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except json.JSONDecodeError:
            state = {}
    trace_events: list[dict[str, Any]] = []
    if trace_path.exists():
        for raw in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                trace_events.append(event)

    note_text = build_chat_note(state, trace_events)
    note_path = session_dir / "chat_note.md"
    note_path.write_text(note_text, encoding="utf-8")
    return note_path


def run_openai_planner(
    args: argparse.Namespace,
    goal: str,
    session_dir: Path,
    trace_path: Path,
    registry: AgentToolRegistry,
    *,
    chat_session: bool = False,
    prior_session_state: dict[str, Any] | None = None,
) -> int:
    config = load_config()
    provider_name = args.provider or config.get("provider.default", "openai")
    model = args.model or config.get(f"{provider_name}.model")
    if provider_name != "openai":
        raise ValueError(f"unsupported planner provider: {provider_name}")
    if not model:
        raise ValueError("OpenAI model is not configured. Run `aiswmm model --provider openai --model gpt-5.5`.")

    # PRD-X: bind a per-process MCP pool so list_tools / call_tool against
    # local servers reuse one long-running node child per server instead of
    # spawning on every call. Lazy — pool only spawns servers on first use.
    ensure_session_pool()

    provider = OpenAIProvider(model=model)

    _agent_say("aiswmm executor")
    _agent_say(f"Goal: {goal}")
    _agent_say(f"Planner: openai ({model})")
    _agent_say(f"Evidence folder: {_display_path(session_dir)}")
    if args.verbose:
        _agent_say(f"Allowed tools: {', '.join(registry.sorted_names())}")

    # Late import keeps the agent runtime free of a CLI-layer dependency
    # in the import graph (commands/agent.py imports runtime_loop).
    from agentic_swmm.commands.agent import resolve_profile_from_args

    profile = resolve_profile_from_args(args)
    executor = AgentExecutor(
        registry,
        session_dir=session_dir,
        trace_path=trace_path,
        dry_run=args.dry_run,
        profile=profile,
    )
    extras = _build_system_prompt_extras(
        session_dir=session_dir,
        prior_session_state=prior_session_state,
    )
    outcome = run_openai_plan(
        goal=goal,
        model=model,
        provider=provider,
        registry=registry,
        executor=executor,
        max_steps=args.max_steps,
        trace_path=trace_path,
        verbose=args.verbose,
        emit=_agent_say,
        prior_session_state=prior_session_state,
        system_prompt_extras=extras,
    )

    if chat_session:
        # Persist a minimal session_state.json so the chat-note generator
        # has structured context to work with.
        state_path = session_dir / "session_state.json"
        if not state_path.exists():
            state_path.write_text(
                json.dumps(
                    {
                        "goal": goal,
                        "status": "ok" if outcome.ok else "fail",
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        chat_note = _write_chat_note_for_session(session_dir)
        _write_event(
            trace_path,
            {
                "event": "session_end",
                "ok": outcome.ok,
                "chat_note": str(chat_note) if chat_note else None,
                "final_text": outcome.final_text,
            },
        )
        _sync_session_end(session_dir)
        _refresh_moc_after_session(session_dir)
        if chat_note is not None:
            _agent_say(f"Chat note: {_display_path(chat_note)}")
        if outcome.final_text:
            _agent_say(outcome.final_text)
        return 0 if outcome.ok else 1

    report = _write_report(
        session_dir,
        goal,
        outcome.plan,
        outcome.results,
        dry_run=args.dry_run,
        allowed_tools=registry.names,
        planner="openai",
        final_text=outcome.final_text,
    )
    _write_event(trace_path, {"event": "session_end", "ok": outcome.ok, "report": str(report), "final_text": outcome.final_text})
    _sync_session_end(session_dir)
    _refresh_moc_after_session(session_dir)
    _agent_say(f"Final report: {_display_path(report)}")
    if outcome.final_text:
        _agent_say(outcome.final_text)
    # CONCURRENCY-OWNER: PRD-TUI-REDESIGN
    # Final retro-chrome result card. Renders the existing outcome /
    # run-dir / metrics / artifacts / boundary / next structure inside
    # a rounded frame; plain-mode users see the same fields without
    # the frame characters.
    from agentic_swmm.agent.reporting import render_result_card_from_run as _render_card

    print(
        _render_card(
            session_dir=session_dir,
            results=outcome.results,
            dry_run=args.dry_run,
        )
    )
    return 0 if outcome.ok else 1


def _looks_like_run_continuation(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        token in lowered
        for token in (
            "plot",
            "figure",
            "graph",
            "rainfall",
            "node",
            "outfall",
            "total_inflow",
            "depth_above_invert",
            "volume_stored_ponded",
            "flow_lost_flooding",
            "hydraulic_head",
            "作图",
            "画图",
            "图",
            "节点",
            "根据你刚才",
            "刚才的运行",
        )
    )


# Issue #59 (UX-4): first-message warm intro classifier + hook.
#
# Task verbs cover EN + ZH. Any one of these in the *first* prompt
# means the user already told us what they want; the planner should
# dispatch directly. The Chinese verbs match common phrasings users
# type unprompted (跑/做/建/检/测/校准 + 审计).
_TASK_VERB_TOKENS: tuple[str, ...] = (
    "run",
    "build",
    "plot",
    "calibrate",
    "audit",
    "check",
    "test",
    "compare",
    "simulate",
    "execute",
    "inspect",
    "summarize",
    "show",
    "list",
    "read",
    "create",
    "generate",
    "make",
    "fix",
    "跑",
    "做",
    "建",
    "审计",
    "校准",
    "验证",
    "对比",
    "比较",
    "运行",
)

# Greeting / identity-probe tokens. Match as substring on the lowered
# prompt so "Hi!", "Hello there", and "你好啊" all classify open-shaped.
_OPEN_GREETING_TOKENS: tuple[str, ...] = (
    "你好",
    "您好",
    "hi",
    "hello",
    "hey",
    "yo",
    "what can you do",
    "what are you",
    "who are you",
    "tell me about yourself",
    "tell me what you",
    "what do you do",
    "introduce yourself",
)


def is_open_shaped_prompt(prompt: str) -> bool:
    """Return True iff ``prompt`` looks open-shaped on the first turn.

    Open-shaped covers three cases that all justify the warm intro:

    1. Greetings (``hi`` / ``hello`` / ``你好`` / ...).
    2. Identity questions (``what can you do`` / ``who are you`` / ...).
    3. Short or verbless prompts (< 5 words AND no task verb).

    A prompt is *task-shaped* (returns False) the moment it contains
    one of the EN/ZH task verbs in ``_TASK_VERB_TOKENS``. This keeps
    "run the tecnopolo demo" from triggering the intro even though it
    is short.
    """
    text = prompt.strip()
    if not text:
        return True
    lowered = text.lower()

    # Task verbs win first — they are an unambiguous signal that the
    # user wants us to act. ZH tokens stay as-is (no case folding).
    if _contains_task_verb(lowered, text):
        return False

    # Explicit greetings / identity probes.
    if any(token in lowered for token in _OPEN_GREETING_TOKENS):
        return True

    # Fallback: a very short prompt without any task verb is treated
    # as open-shaped. Splitting on whitespace handles both EN ("hi
    # there") and the common ZH case where users type a single hanzi
    # phrase like "在吗" or "ready".
    if len(lowered.split()) < 5:
        return True

    return False


def _contains_task_verb(lowered: str, raw: str) -> bool:
    """Substring-match task verbs against the prompt.

    EN tokens are checked against the lowercased text; ZH tokens are
    checked against the raw text so we don't normalise away hanzi.
    """
    for token in _TASK_VERB_TOKENS:
        if token.isascii():
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return True
        else:
            if token in raw:
                return True
    return False


def maybe_warm_intro(prompt: str, *, turn: int) -> str | None:
    """Return the warm-intro text for the first message, or None.

    Returns ``None`` (so the runtime falls through to the normal
    planner dispatch) when:

    - ``turn`` is not 1 (intro only ever fires on the first message),
    - ``AISWMM_DISABLE_WELCOME=1`` is set (consistent with UX-2 #57),
    - or the prompt is task-shaped (``is_open_shaped_prompt`` is False).

    The template itself lives in ``agentic_swmm.agent.prompts`` so the
    string and the runtime hook can be tested in isolation.
    """
    if turn != 1:
        return None
    if _welcome_disabled():
        return None
    if not is_open_shaped_prompt(prompt):
        return None
    return WARM_INTRO_TEMPLATE


def _welcome_disabled() -> bool:
    """Mirror ``welcome._is_disabled`` so the same env var controls both.

    Kept as a tiny local helper instead of importing ``welcome._is_disabled``
    directly because that one is module-private — referring to a private
    name across modules would couple us to its location.
    """
    value = os.environ.get("AISWMM_DISABLE_WELCOME")
    if value is None:
        return False
    return value.strip() not in {"", "0", "false", "False", "no", "No"}


def _is_swmm_run_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if (path / "manifest.json").exists() and ((path / "05_runner").exists() or (path / "01_runner").exists()):
        return True
    return any(path.glob("**/*.out")) and any(path.glob("**/*.rpt"))


def _load_prior_session_state(active_run_dir: Path | None) -> dict[str, Any] | None:
    """Load the previous turn's ``aiswmm_state.json`` if it exists.

    The planner consumes this through ``should_introspect`` to skip
    re-emitting ``list_skills`` / ``list_mcp_*`` calls that the prior
    turn already made. Returns ``None`` when nothing is available so
    the planner falls back to its full introspection on the first turn
    of a fresh case.
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


# --- PRD session-db-facts: startup injection + end-of-session sync -----------


# Module-level set tracking sessions already synced. Both the
# end-of-session hook and ``atexit`` consult this to avoid double-writes
# (idempotent inserts make this cheap, but skipping the trip is nicer).
_SYNCED_SESSION_DIRS: set[str] = set()


def _build_system_prompt_extras(
    *,
    session_dir: Path,
    prior_session_state: dict[str, Any] | None,
) -> list[str]:
    """Assemble the per-session system-prompt injections.

    Order: project facts first (durable user-curated context), then the
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


def _sync_session_end(session_dir: Path) -> None:
    """Sync the just-finished session into the SQLite store.

    Idempotent and silent: per-session double-call is a cheap no-op
    thanks to the unique indices, and any IO failure is swallowed so
    the user's turn return code is unaffected.
    """
    key = str(session_dir.resolve()) if session_dir else ""
    if not key or key in _SYNCED_SESSION_DIRS:
        return
    try:
        sync_session_to_db(session_dir)
    except Exception:
        return
    finally:
        _SYNCED_SESSION_DIRS.add(key)


def _resolve_runs_root_for(session_dir: Path) -> Path:
    """Return the ``runs/`` root that the MOC should describe.

    Order:
      1. ``AISWMM_RUNS_ROOT`` env var (lets tests point at a tmp tree).
      2. The first ancestor of ``session_dir`` named ``runs``.
      3. ``repo_root() / "runs"`` as a last-resort fallback.

    Mirrors the resolution used by ``commands/audit._runs_root_for`` so
    the session-end and force-refresh paths agree.
    """
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


def _refresh_moc_after_session(session_dir: Path) -> None:
    """Regenerate ``runs/INDEX.md`` after a session ends.

    Best-effort: the 'living memory' MOC promise (issue #60) requires
    that every session-end pass leaves a fresh ``runs/INDEX.md`` on
    disk so Obsidian shows new chat notes immediately. We deliberately
    swallow any error and log a single warning — MOC regen must NEVER
    block the user's turn from exiting cleanly.
    """
    try:
        runs_root = _resolve_runs_root_for(session_dir)
        if not runs_root.exists():
            return
        text = generate_moc(runs_root)
        index_path = runs_root / "INDEX.md"
        index_path.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — best-effort, see docstring
        _log.warning("MOC refresh failed for runs/INDEX.md: %s", exc)


def _atexit_sync_recent_sessions() -> None:
    """Belt-and-suspenders: re-sync any session we already touched.

    The end-of-session hook is the primary write path. This atexit
    handler exists for the crash case — process exits before the hook
    fires (Ctrl-C, OOM kill, etc.). It walks every session dir we have
    seen in this process and runs the projector again; the unique
    indices guarantee idempotency.
    """
    for raw in list(_SYNCED_SESSION_DIRS):
        try:
            sync_session_to_db(Path(raw))
        except Exception:
            continue


atexit.register(_atexit_sync_recent_sessions)


# ---------------------------------------------------------------------------
# Retro-chrome tool-execution banners (PRD-TUI-REDESIGN).
# CONCURRENCY-OWNER: PRD-TUI-REDESIGN
# ---------------------------------------------------------------------------
#
# ``execute_with_chrome`` wraps a single ``executor.execute(call)`` invocation
# in the retro start/end banners required by PRD-TUI-REDESIGN:
#
#     [SYS] EXECUTING <tool_name>            ← phosphor green, before
#     ...the tool runs...
#     [INF] COMPLETE <tool_name> (1.84s)     ← phosphor green, after
#     [ERR] FAILED   <tool_name> (0.42s)     ← red, after, if it raised
#
# The helper is exposed at module scope (rather than being inlined into
# ``run_openai_planner``) so the integration test can exercise it
# directly without spinning up a planner. Plain mode strips the
# ``[SYS]/[INF]/[ERR]`` prefixes; the banner is still emitted so timing
# information remains visible in CI logs.


def execute_with_chrome(
    executor: AgentExecutor,
    call,
    *,
    index: int | None = None,
    stream=None,
) -> dict[str, Any]:
    """Run ``executor.execute(call)`` wrapped in retro chrome banners.

    Emits ``[SYS] EXECUTING <tool>`` before, ``[INF] COMPLETE`` /
    ``[ERR] FAILED`` after. Elapsed-time stamp is always shown so a
    user reading the scrollback can see which tool spent the budget.

    The helper preserves ``executor.execute``'s "return a dict, never
    raise" contract: a failed call returns the executor's error dict,
    and the ``[ERR] FAILED`` banner reflects ``result.get("ok")``. If
    the executor itself raises (a programmer error, not a tool-level
    failure), the exception is re-raised after printing ``[ERR]``.
    """
    out = stream if stream is not None else _sys.stdout
    tool_name = call.name
    print(_chrome.sys(f"EXECUTING {tool_name}"), file=out, flush=True)
    t0 = time.monotonic()
    try:
        result = executor.execute(call, index=index)
    except Exception:
        elapsed = time.monotonic() - t0
        print(
            _chrome.err(f"FAILED    {tool_name}  ({elapsed:.2f}s)"),
            file=out,
            flush=True,
        )
        raise
    elapsed = time.monotonic() - t0
    if isinstance(result, dict) and not result.get("ok", True):
        print(
            _chrome.err(f"FAILED    {tool_name}  ({elapsed:.2f}s)"),
            file=out,
            flush=True,
        )
    else:
        print(
            _chrome.inf(f"COMPLETE  {tool_name}  ({elapsed:.2f}s)"),
            file=out,
            flush=True,
        )
    return result


# ---------------------------------------------------------------------------
# Gap-fill interception (PRD-GF-CORE).
# CONCURRENCY-OWNER: PRD-GF-CORE
# ---------------------------------------------------------------------------
#
# The functions below own the runtime-side state machine for L1 (missing
# file paths) and L3 (missing parameter values) gaps. The contract:
#
#   invoke_tool_with_gap_fill(spec, call, session_dir, base_invoke)
#       └── pre-flight L1 scan over `spec.required_file_args`
#       └── call `base_invoke(call, session_dir)` to run the tool
#       └── if result.gap_signal: collect, propose, ui-review, record,
#           re-invoke the tool with merged args
#       └── attach `gap_filled: [...]` to the final success result so
#           the LLM sees what was filled in
#
# The wrapper is invoked from `tool_registry.AgentToolRegistry.execute`
# (also marked CONCURRENCY-OWNER: PRD-GF-CORE). The split keeps the
# orchestration logic out of `tool_registry.py` while the registry
# stays the actual dispatch seam.
#
# Bug class to watch: if the wrapper raises (proposer registry-only
# miss, UI rejection), the runtime returns a fail-soft result dict
# rather than propagating — the planner's contract is "execute()
# returns a dict, never raises". The error is folded into `summary`
# and `ok=False`.


def _gap_fill_disabled() -> bool:
    """Return True iff the operator has set ``AISWMM_GAP_DISABLE=1``.

    PRD-GF-CORE ships a per-tool ``supports_gap_fill`` flag *and* a
    global kill-switch so a regression can be rolled back without a
    code revert.
    """
    value = os.environ.get("AISWMM_GAP_DISABLE")
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no"}


def _is_tty() -> bool:
    """Return True iff both stdin and stdout look like a real TTY.

    The UI uses this to decide whether to render the batched form or
    fall through to the env-var matrix. Tests can drive the matrix
    by setting the matching env vars; production CI hits the non-TTY
    branch automatically.
    """
    try:
        import sys as _sys

        return bool(_sys.stdin.isatty() and _sys.stdout.isatty())
    except Exception:
        return False


def invoke_tool_with_gap_fill(spec, call, session_dir, base_invoke):
    """Run ``spec.handler`` through the L1+L3 gap-fill state machine.

    The wrapper is no-op (just calls ``base_invoke``) when:

    - ``spec.supports_gap_fill`` is False, or
    - ``AISWMM_GAP_DISABLE=1`` is set, or
    - the spec has no declared ``required_file_args`` AND the first
      result has no ``gap_signal`` (i.e. no gaps to fill).

    Otherwise the wrapper runs the full detect → propose → review →
    record → retry loop and returns the resumed tool's result with a
    ``gap_filled: [...]`` field appended.

    Parameters:

    - ``spec``: the :class:`agentic_swmm.agent.tool_registry.ToolSpec`.
    - ``call``: the :class:`agentic_swmm.agent.types.ToolCall`.
    - ``session_dir``: the per-session run directory (used for
      audit writes).
    - ``base_invoke``: a callable ``(call, session_dir) -> dict``
      that actually runs the handler. Decoupled so the registry's
      pre-call permission/profile checks stay in one place.
    """
    if _gap_fill_disabled() or not getattr(spec, "supports_gap_fill", False):
        return base_invoke(call, session_dir)

    # Imports kept local — gap-fill modules are only needed when the
    # wrapper actually fires, and a missing pyyaml on a minimal venv
    # should not break tool_registry import-time.
    from agentic_swmm.gap_fill.preflight import scan_required_files
    from agentic_swmm.gap_fill.proposer import GapFillRegistryOnlyMiss, propose_batch
    from agentic_swmm.gap_fill.protocol import GapSignal
    from agentic_swmm.gap_fill.recorder import record_gap_decisions
    from agentic_swmm.gap_fill.ui import (
        GapFillNonInteractive,
        GapFillRejected,
        review_batch,
    )

    merged_args: dict[str, object] = dict(call.args)
    all_resolved = []
    # Two retries max: one for pre-flight L1, one for in-band L3, plus
    # a guard so a buggy tool that keeps emitting the same gap can't
    # loop forever. The PRD allows L1+L3 in one batch but we keep the
    # iteration count tight.
    for attempt in range(3):
        # Pre-flight L1 scan happens BEFORE the tool runs so we never
        # invoke a tool with a path that won't open.
        l1_signals = []
        required = getattr(spec, "required_file_args", ()) or ()
        if required:
            l1_signals = scan_required_files(
                tool_name=spec.name,
                required_file_args=required,
                args=merged_args,
            )

        if l1_signals:
            resolved = _resolve_gap_batch(
                l1_signals,
                tool_name=spec.name,
                session_dir=session_dir,
                propose_batch=propose_batch,
                review_batch=review_batch,
                record_gap_decisions=record_gap_decisions,
            )
            if resolved is None:
                # User rejected / non-interactive failure — fall back
                # to a fail-soft result the planner can surface.
                return {
                    "tool": call.name,
                    "args": call.args,
                    "ok": False,
                    "summary": "gap-fill aborted (L1 paths could not be resolved)",
                }
            for dec in resolved:
                merged_args[dec.field] = dec.final_value
            all_resolved.extend(resolved)
            # Loop back to re-run the pre-flight scan (in case the
            # new path itself doesn't exist).
            continue

        # Now run the tool.
        from agentic_swmm.agent.types import ToolCall as _ToolCall

        invocation = _ToolCall(name=call.name, args=dict(merged_args))
        try:
            result = base_invoke(invocation, session_dir)
        except (GapFillRejected, GapFillNonInteractive, GapFillRegistryOnlyMiss) as exc:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": f"gap-fill aborted: {exc}",
                "return_code": 1,
            }

        gap_payload = result.get("gap_signal") if isinstance(result, dict) else None
        if not gap_payload:
            # Tool succeeded (or failed for non-gap reasons). Attach
            # the cumulative gap_filled list and return.
            if all_resolved and isinstance(result, dict) and result.get("ok"):
                result = dict(result)
                result["gap_filled"] = [
                    {
                        "field": d.field,
                        "final_value": d.final_value,
                        "source": d.proposer.source,
                        "decision_id": d.decision_id,
                    }
                    for d in all_resolved
                ]
            return result

        try:
            signal = GapSignal.from_dict(gap_payload)
        except (ValueError, TypeError):
            # Tool emitted a malformed gap_signal; surface as failure
            # rather than guessing.
            return result

        resolved = _resolve_gap_batch(
            [signal],
            tool_name=spec.name,
            session_dir=session_dir,
            propose_batch=propose_batch,
            review_batch=review_batch,
            record_gap_decisions=record_gap_decisions,
        )
        if resolved is None:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": "gap-fill aborted (L3 parameters could not be resolved)",
            }
        for dec in resolved:
            merged_args[dec.field] = dec.final_value
        all_resolved.extend(resolved)

    # Out of retries — the tool keeps emitting gap signals. Return a
    # loud failure so the planner doesn't loop forever upstream.
    return {
        "tool": call.name,
        "args": call.args,
        "ok": False,
        "summary": (
            "gap-fill retry budget exhausted after 3 attempts; "
            "the tool kept emitting gap_signal"
        ),
    }


def _resolve_gap_batch(
    signals,
    *,
    tool_name,
    session_dir,
    propose_batch,
    review_batch,
    record_gap_decisions,
):
    """Run propose → ui → record over a batch of signals.

    Returns the list of :class:`GapDecision` with ``final_value`` set,
    or ``None`` if the user rejected the batch or the non-interactive
    path failed. Errors are folded to ``None`` so the caller can
    return a fail-soft result; the exception messages are written to
    the planner trace via stderr.
    """
    try:
        proposals = propose_batch(
            signals=signals,
            run_dir=session_dir,
            llm_proposal_fn=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        import sys as _sys

        _sys.stderr.write(f"GAP_FILL_PROPOSE_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    try:
        resolved = review_batch(
            proposals,
            tool_name=tool_name,
            is_tty=_is_tty(),
        )
    except Exception as exc:
        import sys as _sys

        _sys.stderr.write(f"GAP_FILL_UI_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    try:
        recorded = record_gap_decisions(session_dir, resolved)
    except Exception as exc:  # pragma: no cover - defensive
        import sys as _sys

        _sys.stderr.write(f"GAP_FILL_RECORD_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    return recorded
