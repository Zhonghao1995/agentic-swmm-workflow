"""Interactive shell facade + OpenAI planner turn driver for ``aiswmm``.

PRD-02 split this file into deeper modules:

- :mod:`agentic_swmm.agent.repl` — REPL input/dispatch loop.
- :mod:`agentic_swmm.agent.warm_intro` — warm-intro state machine.
- :mod:`agentic_swmm.agent.session_bootstrap` — date-dir + slug + naming
  helpers.

This module is now the facade that boots the REPL with real
collaborators (real ``input``, real planner) and continues to host the
single-turn OpenAI planner driver used by both interactive and
non-interactive flows. The audit PRD's chat-only-turn hook
(``_write_chat_note_for_session``) lives here unchanged: chat-style
turns persist a ``session_state.json`` skeleton and an Obsidian-ready
``chat_note.md`` next to the agent trace.

Public-name compatibility: every name external callers used before
PRD-02 (``is_open_shaped_prompt``, ``maybe_warm_intro``,
``WARM_INTRO_TEMPLATE``, ``format_startup_banner``,
``run_interactive_shell``, ``run_openai_planner``, ``_case_slug``,
``_new_interactive_session``, ``_display_path``, ``_safe_name``,
``_refresh_moc_after_session``, ``_build_system_prompt_extras``,
``invoke_tool_with_gap_fill``, ``execute_with_chrome``, ``_is_tty``,
``OpenAIProvider``, ``load_config``, ``generate_moc``) is re-exported
from here, so existing imports and ``unittest.mock.patch`` targets
continue to work without changes.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import sys as _sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent import tui_chrome as _chrome
from agentic_swmm.agent import ui_colors
from agentic_swmm.agent import welcome as _welcome
from agentic_swmm.agent.digest_render import render_final_summary
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_classifier import classify_intent
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
from agentic_swmm.memory.session_sync import sync_session_to_db
from agentic_swmm.providers.factory import make_provider
from agentic_swmm.providers.openai_api import OpenAIProvider
from agentic_swmm.utils.paths import repo_root

# PRD-02 — deep-module split. New modules with the carved-out behaviour;
# names below are re-exported so legacy imports continue to resolve.
from agentic_swmm.agent.repl import run_repl
from agentic_swmm.agent.session_bootstrap import (
    bootstrap_prior_state as _bootstrap_prior_state,
    bootstrap_runs_root as _bootstrap_runs_root,
    bootstrap_session_dir as _bootstrap_session_dir,
    bootstrap_system_prompt as _bootstrap_system_prompt,
    infer_case_slug as _case_slug,
    is_swmm_run_dir as _bootstrap_is_swmm_run_dir,
    new_interactive_session as _new_interactive_session,
)
from agentic_swmm.agent.warm_intro import (
    WarmIntroState,
    maybe_emit_warm_intro,
)

_log = logging.getLogger(__name__)

# ``_safe_name`` was previously re-exported from ``single_shot`` here.
# Tests and callers continue to import it from this module unchanged.
from agentic_swmm.agent.single_shot import _safe_name


def run_interactive_shell(args: argparse.Namespace) -> int:
    """Boot the interactive shell and hand control to the REPL.

    This function owns the boot-time concerns:

    - argument validation (``--planner openai`` required),
    - root run-folder resolution (``args.session_dir`` or ``repo_root()/runs``),
    - first-session bootstrap (``_new_interactive_session``),
    - welcome banner + startup banner.

    After that, it delegates the input → dispatch → planner loop to
    :func:`agentic_swmm.agent.repl.run_repl` with real collaborators
    (real ``input``, real ``_run_planner_for_prompt`` planner runner).
    """
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
    _agent_say(
        format_startup_banner(
            session_label=session_label,
            date_dir_display=_display_path(date_dir),
            profile_name=profile_name,
        )
    )

    # Per-turn planner runner: dispatches each prompt through the real
    # OpenAI planner with the proper session-dir + chat-vs-run choice.
    # The closure captures ``date_dir`` (and the mutable ``active_run_dir``
    # box) so the REPL stays agnostic of these concerns.
    active_run_dir: list[Path | None] = [None]
    # ``date_dir`` is a list-of-one so the ``/new-session`` callback can
    # rebind it without losing closure scope. ``session_label`` lives
    # in the same shape for the user-visible banner string.
    date_dir_box: list[Path] = [date_dir]
    session_label_box: list[str] = [session_label]

    def on_new_session() -> None:
        new_date_dir, new_label = _new_interactive_session(base_dir)
        date_dir_box[0] = new_date_dir
        session_label_box[0] = new_label
        active_run_dir[0] = None
        _agent_say(f"New session: {new_label}")
        _agent_say(f"Date folder: {_display_path(new_date_dir)}\n")

    def planner_runner(
        run_args: argparse.Namespace,
        prompt: str,
        _placeholder_session_dir: Path,
        _placeholder_trace_path: Path,
        _placeholder_registry: Any,
        *,
        chat_session: bool = False,
        prior_session_state: dict[str, Any] | None = None,
    ) -> int:
        use_active_run = (
            active_run_dir[0] is not None and _looks_like_run_continuation(prompt)
        )
        goal = prompt
        is_chat_turn = False
        if use_active_run:
            session_dir = active_run_dir[0]
            assert session_dir is not None  # mypy
            goal = f"{prompt}\n\nPrevious run directory: {session_dir}"
        elif _looks_like_swmm_request(prompt):
            session_dir = _bootstrap_session_dir(date_dir_box[0], prompt, kind="run")
        else:
            session_dir = _bootstrap_session_dir(date_dir_box[0], prompt, kind="chat")
            is_chat_turn = True
        session_dir.mkdir(parents=True, exist_ok=True)
        trace_path = session_dir / "agent_trace.jsonl"
        prior_state = _bootstrap_prior_state(active_run_dir[0])
        print()
        rc = run_openai_planner(
            run_args,
            goal,
            session_dir,
            trace_path,
            AgentToolRegistry(),
            chat_session=is_chat_turn,
            prior_session_state=prior_state,
        )
        if rc == 0 and _bootstrap_is_swmm_run_dir(session_dir):
            active_run_dir[0] = session_dir
        print()
        return rc

    return run_repl(
        args,
        base_dir=base_dir,
        profile_name=profile_name,
        input_source=input,
        planner_runner=planner_runner,
        output=_agent_say,
        on_new_session=on_new_session,
    )


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


def _write_chat_note_for_session(session_dir: Path) -> Path | None:
    """Write ``chat_note.md`` for a chat-only session.

    The audit PRD (M8) requires chat sessions to carry an Obsidian-ready
    ``chat_note.md`` alongside ``session_state.json`` and
    ``agent_trace.jsonl``. We skip this for SWMM run dirs so the audit
    note remains the canonical record there.
    """
    if not session_dir.exists() or not session_dir.is_dir():
        return None
    if _bootstrap_is_swmm_run_dir(session_dir):
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
    if provider_name not in ("openai", "claude_sdk"):
        raise ValueError(f"unsupported planner provider: {provider_name}")
    if not model:
        if provider_name == "claude_sdk":
            raise ValueError(
                "claude_sdk model is not configured. Run "
                "`aiswmm model --provider claude_sdk --model claude-sonnet-4-5-20250929`."
            )
        raise ValueError("OpenAI model is not configured. Run `aiswmm model --provider openai --model gpt-5.5-2026-04-23`.")

    # PRD-X: bind a per-process MCP pool so list_tools / call_tool against
    # local servers reuse one long-running node child per server instead of
    # spawning on every call. Lazy — pool only spawns servers on first use.
    ensure_session_pool()

    # PRD-09: route construction through the provider factory. The
    # ``openai`` path still goes through the module-level
    # ``OpenAIProvider`` symbol so existing ``mock.patch`` targets keep
    # working; only the ``claude_sdk`` path takes the factory branch.
    if provider_name == "openai":
        provider = OpenAIProvider(model=model)
    else:
        provider = make_provider(provider_name, model=model)

    _agent_say("aiswmm executor")
    _agent_say(f"Goal: {goal}")
    _agent_say(f"Planner: {provider_name} ({model})")
    _agent_say(f"Evidence folder: {_display_path(session_dir)}")
    if args.verbose:
        _agent_say(f"Allowed tools: {', '.join(registry.sorted_names())}")

    # PRD-08 A.3 (audit #21): emit a ``user_prompt`` event at the top
    # of every planner turn so the chat-note renderer can populate
    # "What user asked". Previously the trace only carried tool calls
    # and the final answer, so chat_note showed "(no user prompts
    # recorded)" for every interactive session.
    _write_event(
        trace_path,
        {
            "event": "user_prompt",
            "text": goal,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )

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
        verbose=bool(getattr(args, "verbose", False)),
    )
    extras = _bootstrap_system_prompt(
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
    if args.verbose:
        # CONCURRENCY-OWNER: PRD-TUI-REDESIGN
        # Verbose path keeps the rounded-frame ``[SYS] RUN COMPLETE``
        # card unchanged (debugging surface is sacred per PRD-185).
        from agentic_swmm.agent.reporting import render_result_card_from_run as _render_card

        print(
            _render_card(
                session_dir=session_dir,
                results=outcome.results,
                dry_run=args.dry_run,
            )
        )
    else:
        # PRD-185 digest mode: emit the compact Peak / Continuity /
        # Run dir block whenever the session produced a manifest.json
        # (chat-only sessions naturally render no block).
        summary_block = render_final_summary([session_dir])
        if summary_block:
            print(summary_block)
    return 0 if outcome.ok else 1


def _looks_like_run_continuation(prompt: str) -> bool:
    """Return True iff ``prompt`` is plot-vocab-shaped (mid-run continuation).

    PRD #121: keyword vocabulary lives in
    ``agentic_swmm.agent.intent_classifier``. This wrapper preserves
    the exact byte-for-byte behaviour of the previous inline tuple.
    """
    return classify_intent(prompt).looks_like_run_continuation


# Issue #59 (UX-4) / PRD-02:
#
# ``is_open_shaped_prompt`` and ``maybe_warm_intro`` are re-exported
# here so the warm-intro public API stays the same. The deep
# implementation lives in :mod:`agentic_swmm.agent.warm_intro`.


def is_open_shaped_prompt(prompt: str) -> bool:
    """Return True iff ``prompt`` looks open-shaped on the first turn.

    Open-shaped covers three cases that all justify the warm intro:

    1. Greetings (``hi`` / ``hello`` / ``你好`` / ...).
    2. Identity questions (``what can you do`` / ``who are you`` / ...).
    3. Short or verbless prompts (< 5 words AND no task verb).
    """
    return classify_intent(prompt).is_open_shaped


def maybe_warm_intro(prompt: str, *, turn: int) -> str | None:
    """Legacy facade — return the warm-intro template on the first turn, or None.

    PRD-02 superseded this with the explicit :class:`WarmIntroState`
    state machine in :mod:`agentic_swmm.agent.warm_intro`. Callers
    that still use the ``turn`` integer can keep doing so: ``turn != 1``
    short-circuits to None; ``turn == 1`` delegates to the new
    state-machine emit (with a fresh, throwaway state — the per-call
    semantics match the old function).

    Returns ``None`` when:

    - ``turn`` is not 1,
    - ``AISWMM_DISABLE_WELCOME=1`` is set,
    - or the prompt is task-shaped.
    """
    if turn != 1:
        return None
    return maybe_emit_warm_intro(WarmIntroState(), prompt)


def _welcome_disabled() -> bool:
    """Mirror ``welcome._is_disabled`` so the same env var controls both."""
    value = os.environ.get("AISWMM_DISABLE_WELCOME")
    if value is None:
        return False
    return value.strip() not in {"", "0", "false", "False", "no", "No"}


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
    """Facade over :func:`session_bootstrap.bootstrap_system_prompt`.

    Kept so any external caller / ``mock.patch`` target that still
    points at this name keeps working — including
    ``tests/test_runtime_loop_previous_session_injection.py``. New
    call sites should import ``bootstrap_system_prompt`` directly.
    """
    return _bootstrap_system_prompt(
        session_dir=session_dir,
        prior_session_state=prior_session_state,
    )


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


def _refresh_moc_after_session(session_dir: Path) -> None:
    """Regenerate ``runs/INDEX.md`` after a session ends.

    Best-effort: the 'living memory' MOC promise (issue #60) requires
    that every session-end pass leaves a fresh ``runs/INDEX.md`` on
    disk so Obsidian shows new chat notes immediately. We deliberately
    swallow any error and log a single warning — MOC regen must NEVER
    block the user's turn from exiting cleanly.
    """
    try:
        runs_root = _bootstrap_runs_root(session_dir)
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
# Gap-fill interception (PRD-GF-CORE) — extracted to
# :mod:`agentic_swmm.agent.gap_fill_runtime` (Issue #205).
# CONCURRENCY-OWNER: PRD-GF-CORE
# ---------------------------------------------------------------------------
# Names re-exported here so existing call sites
# (``tool_registry.AgentToolRegistry.execute``) and ``mock.patch``
# targets (``runtime_loop._is_tty``) continue to resolve unchanged.
from agentic_swmm.agent.gap_fill_runtime import (
    gap_fill_disabled as _gap_fill_disabled,  # noqa: F401 — back-compat re-export
    invoke_tool_with_gap_fill,
    is_tty as _is_tty,
)
