"""Interactive shell facade + OpenAI planner turn driver for ``aiswmm``.

PRD-02 and Issue #205 split this file into deeper modules:

- :mod:`agentic_swmm.agent.repl` — REPL input/dispatch loop.
- :mod:`agentic_swmm.agent.warm_intro` — warm-intro state machine.
- :mod:`agentic_swmm.agent.session_bootstrap` — session-lifecycle
  bootstrap phases (``bootstrap_session_dir`` /
  ``bootstrap_prior_state`` / ``bootstrap_system_prompt`` /
  ``bootstrap_runs_root``) and the path-naming primitives that PRD-02
  carved out (``safe_name``, ``infer_case_slug``,
  ``new_interactive_session``).
- :mod:`agentic_swmm.agent.gap_fill_runtime` — gap-fill state machine
  (``invoke_tool_with_gap_fill``, ``is_tty``, ``gap_fill_disabled``).

This module is now the facade that boots the REPL with real
collaborators (real ``input``, real planner) and continues to host the
single-turn OpenAI planner driver used by both interactive and
non-interactive flows. The audit PRD's chat-only-turn hook
(``_write_chat_note_for_session``) lives here unchanged: chat-style
turns persist a ``session_state.json`` skeleton and an Obsidian-ready
``chat_note.md`` next to the agent trace.

Public-name compatibility: every name external callers used before
PRD-02 (``is_open_shaped_prompt``, ``maybe_warm_intro``,
``WARM_INTRO_TEMPLATE``,
``run_interactive_shell``, ``run_openai_planner``, ``_case_slug``,
``_new_interactive_session``, ``_display_path``, ``_safe_name``,
``_refresh_moc_after_session``, ``_build_system_prompt_extras``,
``invoke_tool_with_gap_fill``, ``execute_with_chrome``, ``_is_tty``,
``load_config``, ``generate_moc``) is re-exported from here, so
existing imports and ``unittest.mock.patch`` targets continue to work
without changes. Provider construction routes through
``make_provider`` (also re-exported) for every backend.
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
from agentic_swmm.agent.error_boundary import on_exception_return_default
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_classifier import classify_intent
from agentic_swmm.agent.mcp_pool import ensure_session_pool
from agentic_swmm.agent.intent_classifier import (
    looks_like_new_modeling_request,
    message_asks_for_input,
    referenced_run_dir,
)
from agentic_swmm.agent.planner import _looks_like_swmm_request
from agentic_swmm.agent.prompts import WARM_INTRO_TEMPLATE
from agentic_swmm.agent.reporting import display_goal, write_event as _write_event
from agentic_swmm.agent.reporting import write_report as _write_report
from agentic_swmm.agent.runtime import run_openai_plan
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.ui import agent_say as _agent_say
from agentic_swmm.agent.ui import display_path as _display_path
from agentic_swmm.audit.chat_note import build_chat_note
from agentic_swmm.audit.moc_generator import generate_moc
from agentic_swmm.agent.session_header import (
    finalize_session_header,
    try_write_session_header,
)
from agentic_swmm.config import DEFAULT_PROVIDER, load_config
from agentic_swmm.memory.session_sync import sync_session_to_db
from agentic_swmm.providers.factory import SUPPORTED_PROVIDERS, make_provider
from agentic_swmm.utils.paths import register_workspace_root, repo_root, resolve_runs_dir

# PRD-02 — deep-module split. New modules with the carved-out behaviour;
# names below are re-exported so legacy imports continue to resolve.
from agentic_swmm.agent.swmm_runtime.run_layout import agent_file, agent_file_for_write
from agentic_swmm.agent.repl import DECLINED_EXIT_CODE, run_repl, ANSWERED_WITH_FAILURES_EXIT_CODE
from agentic_swmm.agent.session_bootstrap import (
    bootstrap_prior_state as _bootstrap_prior_state,
    bootstrap_runs_root as _bootstrap_runs_root,
    bootstrap_session_dir as _bootstrap_session_dir,
    bootstrap_system_prompt as _bootstrap_system_prompt,
    infer_case_slug as _case_slug,
    is_swmm_run_dir as _is_swmm_run_dir,
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

    - argument validation (``--planner llm`` required; ``openai`` accepted
      as the deprecated alias),
    - root run-folder resolution (``args.session_dir`` or ``repo_root()/runs``),
    - first-session bootstrap (``_new_interactive_session``),
    - welcome banner + startup banner.

    After that, it delegates the input → dispatch → planner loop to
    :func:`agentic_swmm.agent.repl.run_repl` with real collaborators
    (real ``input``, real ``_run_planner_for_prompt`` planner runner).
    """
    if args.planner not in ("llm", "openai"):
        raise ValueError("interactive agent shell currently requires `--planner llm`.")

    base_dir = register_workspace_root(args.session_dir.expanduser().resolve() if args.session_dir else resolve_runs_dir())
    base_dir.mkdir(parents=True, exist_ok=True)

    date_dir, session_label = _new_interactive_session(base_dir)

    # Late import keeps the agent runtime free of a CLI-layer dependency
    # in the import graph (commands/agent.py imports runtime_loop).
    from agentic_swmm.commands.agent import resolve_profile_string

    profile_name = resolve_profile_string(args)

    # ADR-0003 leftover: interactive turns get the same session header the
    # single-shot path writes. Provider/model resolved once per shell via
    # the shared selection seam run_openai_planner also applies per turn.
    from agentic_swmm.providers.selection import resolve_selection

    _header_selection = resolve_selection(args.provider, args.model)
    header_provider = _header_selection.route
    header_model = _header_selection.model

    # Issue #57 (UX-2): the welcome module owns the entire first screen.
    # It used to be followed by a second one-line banner from this
    # module, which restated the session label and the profile in order
    # to add the run directory; the directory is now a segment of the
    # welcome header, so those facts are stated once. That second banner
    # also printed unconditionally, leaking through
    # AISWMM_DISABLE_WELCOME, whose acceptance is that a scripted run
    # boots with no banner at all.
    #
    # The welcome owns its own NO_COLOR / AISWMM_DISABLE_WELCOME /
    # first-run-marker handling, and any failure inside it is swallowed
    # (decoration must not block the agent from booting).
    _welcome.print_welcome(
        session_label=session_label,
        profile_name=profile_name,
        run_dir_display=_display_path(date_dir),
    )

    # Per-turn planner runner: dispatches each prompt through the real
    # OpenAI planner with the proper session-dir + chat-vs-run choice.
    # The closure captures ``date_dir`` (and the mutable ``active_run_dir``
    # box) so the REPL stays agnostic of these concerns.
    active_run_dir: list[Path | None] = [None]
    session_run_dirs: list[Path] = []
    # Pending-turn state (BUG-1): after every successful turn we record
    # which session dir it used and how its final message ended, so the
    # NEXT input can be routed as the answer to that message instead of
    # being re-classified from vocabulary alone.
    pending_box: list[dict[str, Any] | None] = [None]
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
        session_run_dirs.clear()
        pending_box[0] = None
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
        # Turn dispatch (rewritten 2026-08-09 after the user's live
        # interactive test, BUG-1/BUG-2 in the bug record): the natural
        # reply to an assistant question carries no task verbs, so the
        # old vocab-first dispatch classified every short confirmation
        # ("use the recommended defaults", a bare bbox) as smalltalk
        # and orphaned the conversation into a fresh chat dir. Order
        # now:
        #   1. A pending previous turn exists and the input is not a
        #      clearly NEW modeling request -> continue THAT turn in
        #      THE SAME session dir, goal composed from the previous
        #      message tail + the reply.
        #   2. An active run exists and the input is not a new request
        #      -> continue the active run (inverted default: continue
        #      unless clearly new, instead of new unless vocab match).
        #   3. New modeling request -> fresh run dir.
        #   4. Fallback -> chat dir.
        goal = prompt
        is_chat_turn = False
        pending = pending_box[0]
        # Finding F-07 (live sessions 2026-09-02): with a turn or run in
        # hand, the broad vocabulary matcher is the wrong question ("node",
        # "map", "plot" describe every follow-up about a finished run, and
        # each one opened an empty folder named after the sentence). Ask
        # instead whether the sentence STARTS new modelling work. With
        # nothing in hand the vocabulary matcher still decides run vs chat.
        if pending is not None or active_run_dir[0] is not None:
            answering = pending is not None and message_asks_for_input(
                str(pending.get("tail") or "")
            )
            new_request = looks_like_new_modeling_request(
                prompt,
                answering_question=answering,
                in_chat=bool(pending.get("is_chat", False)) if pending is not None else False,
            )
        else:
            new_request = _looks_like_swmm_request(prompt)
        earlier = None
        if not new_request:
            earlier = referenced_run_dir(prompt, session_run_dirs, active_run_dir[0])
        if earlier is not None:
            # Live finding F-71 (2026-09-02): the follow-up names one earlier
            # run of this session; re-anchor the turn there.
            session_dir = earlier
            active_run_dir[0] = earlier
            goal = f"{prompt}\n\nPrevious run directory: {session_dir}"
        elif pending is not None and not new_request:
            session_dir = pending["session_dir"]
            is_chat_turn = bool(pending.get("is_chat", False))
            goal = (
                f"{prompt}\n\n[Continuation of the previous turn in this "
                f"session. Your previous message ended with:]\n"
                f"{pending['tail']}"
            )
            if pending.get("run_dir"):
                goal += f"\nPrevious run directory: {pending['run_dir']}"
            if pending.get("failed"):
                goal += FAILED_RUN_NOTE
        elif active_run_dir[0] is not None and not new_request:
            session_dir = active_run_dir[0]
            goal = f"{prompt}\n\nPrevious run directory: {session_dir}"
        elif new_request:
            session_dir = _bootstrap_session_dir(date_dir_box[0], prompt, kind="run")
            if pending is not None and pending.get("is_chat", False):
                # Live finding F-75: the work was asked for in answer to a
                # chat question; keep the conversation, open a run folder.
                goal = (
                    f"{prompt}\n\n[Continuation of the previous turn in this "
                    f"session. Your previous message ended with:]\n"
                    f"{pending['tail']}"
                )
        else:
            session_dir = _bootstrap_session_dir(date_dir_box[0], prompt, kind="chat")
            is_chat_turn = True
        session_dir.mkdir(parents=True, exist_ok=True)
        trace_path = agent_file_for_write(session_dir, "agent_trace.jsonl")
        registry = AgentToolRegistry()
        # The turn dir IS the session dir (agent_trace.jsonl lives here):
        # it gets the ADR-0003 header exactly like a single-shot session.
        # Re-running inside an active run dir refreshes the header with
        # the follow-up goal rather than leaving the original one stale.
        try_write_session_header(
            session_dir,
            goal=prompt,
            planner="llm",
            profile=profile_name,
            provider=header_provider,
            model=header_model,
            registry=registry,
        )
        prior_state = _bootstrap_prior_state(active_run_dir[0])
        outcome_box: list[Any] = []
        print()
        try:
            rc = run_openai_planner(
                run_args,
                goal,
                session_dir,
                trace_path,
                registry,
                chat_session=is_chat_turn,
                prior_session_state=prior_state,
                outcome_box=outcome_box,
            )
        except BaseException as exc:
            finalize_session_header(session_dir, "interrupted")
            # Live finding F-159 (2026-09-05, S27 r2): a run turn that ended in
            # an exception (an upstream build timeout, then a socket timeout)
            # set no anchor at all, so "that run" in the next turn opened a
            # fresh chat and the model borrowed another session's run from
            # memory. The interrupted run stays the anchor like a failed one.
            if not is_chat_turn and _is_swmm_run_dir(session_dir):
                pending_box[0] = _failed_anchor(session_dir, f"error: {exc}")
            raise
        finalize_session_header(session_dir, "completed" if rc == 0 else "failed")
        if rc == 0 and _is_swmm_run_dir(session_dir):
            active_run_dir[0] = session_dir
            if session_dir not in session_run_dirs:
                session_run_dirs.append(session_dir)
        if rc == 0:
            final_text = outcome_box[0] if outcome_box else ""
            pending_box[0] = {
                "session_dir": session_dir,
                "is_chat": is_chat_turn,
                "tail": (final_text or "")[-400:],
                "run_dir": str(active_run_dir[0]) if active_run_dir[0] else None,
            }
        elif not is_chat_turn and _is_swmm_run_dir(session_dir):
            # Live finding F-97 (2026-09-03, S40 r3): a failed run turn left
            # no anchor, the next question about "that run" opened a fresh
            # chat and the model served another run's results as this one.
            # The failed run stays the anchor and the continuation says it
            # produced nothing.
            final_text = outcome_box[0] if outcome_box else ""
            pending_box[0] = _failed_anchor(session_dir, final_text)
        # On a failed chat turn the previous pending state stays: the user
        # can still answer the last question after a crashed turn.
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

    state_path = agent_file_for_write(session_dir, "session_state.json")
    trace_path = agent_file_for_write(session_dir, "agent_trace.jsonl")
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
    # A chat turn produces a folder too, and its reader deserves the same one
    # page as a run's. Best-effort: an index is not worth failing a finished
    # turn.
    try:
        from agentic_swmm.reporting.run_readme import write_run_readme

        write_run_readme(session_dir, goal=display_goal(str(getattr(state, "goal", "") or "")))
    except Exception:
        pass
    return note_path



# Live finding F-63 (2026-09-02): a person who answers "n" to the first
# approval is not a failed turn. DECLINED_EXIT_CODE lives in repl.py (this
# module imports the shell, not the other way round).


def turn_answered_with_failures(outcome: Any) -> bool:
    """True when the turn did not succeed, nothing was declined, the planner
    still gave a final answer, and at least one tool call failed.

    The planner keeps ``ok=False`` when a closing text turn leaves a tool
    failure unresolved (so prose cannot paper over a failure); the shell
    must then say that, not "Turn failed" under a complete answer (live
    finding F-123, 2026-09-03, S55).
    """
    if getattr(outcome, "ok", False):
        return False
    if turn_was_declined(outcome):
        return False
    if not str(getattr(outcome, "final_text", "") or "").strip():
        return False
    for result in getattr(outcome, "results", None) or []:
        if isinstance(result, dict) and not result.get("ok", True):
            return True
    return False


def turn_was_declined(outcome: Any) -> bool:
    """True when the turn did not succeed and at least one prompted tool was declined."""
    if getattr(outcome, "ok", False):
        return False
    for result in getattr(outcome, "results", None) or []:
        permission = result.get("permission") if isinstance(result, dict) else None
        if isinstance(permission, dict) and permission.get("prompted") and not permission.get("approved", True):
            return True
    return False



_SWAP_NOTES_SAID: set[tuple[str, str]] = set()


def _reconcile_model_with_gateway(provider_name: str, model: str | None) -> str | None:
    """Swap a pinned model the gateway no longer offers for an offered sibling.

    The note is said once per process: the shell runs this per turn, and
    the same sentence on every turn of a session was noise (live test
    2026-09-03, S40).
    """
    try:
        from agentic_swmm.agent.provider_preflight import provider_key_value
        from agentic_swmm.providers.model_check import offered_models, reconcile_model, route_spec

        spec = route_spec(provider_name)
        if spec is None or not spec.detect_url:
            return model
        offered = offered_models(spec, key=provider_key_value(provider_name))
        chosen, note = reconcile_model(spec, model, offered)
    except Exception:  # noqa: BLE001 - a probe must never break a session
        return model
    if note:
        key = (provider_name, str(model))
        if key not in _SWAP_NOTES_SAID:
            _SWAP_NOTES_SAID.add(key)
            _agent_say(note)
    return chosen

FAILED_RUN_NOTE = (
    "\n[The previous turn's run in this directory FAILED and produced no results "
    "(no model, no .rpt). If the user asks about that run, say so plainly. Never "
    "present another run's results as this one.]"
)


def _failed_anchor(session_dir: Path, final_text: str) -> dict[str, Any]:
    """Pending state for a run turn that produced nothing (F-97, F-159).

    The failed or interrupted run stays the anchor of the next turn and the
    continuation carries FAILED_RUN_NOTE, so "that run" is answered with the
    truth instead of another run's results.
    """
    return {
        "session_dir": session_dir,
        "is_chat": False,
        "tail": (final_text or "")[-400:],
        "run_dir": str(session_dir),
        "failed": True,
    }


def _exit_code_for(outcome: Any) -> int:
    if getattr(outcome, "ok", False):
        return 0
    if turn_was_declined(outcome):
        return DECLINED_EXIT_CODE
    if turn_answered_with_failures(outcome):
        return ANSWERED_WITH_FAILURES_EXIT_CODE
    return 1

def run_openai_planner(
    args: argparse.Namespace,
    goal: str,
    session_dir: Path,
    trace_path: Path,
    registry: AgentToolRegistry,
    *,
    chat_session: bool = False,
    prior_session_state: dict[str, Any] | None = None,
    outcome_box: list[Any] | None = None,
) -> int:
    from agentic_swmm.providers.selection import resolve_selection

    selection = resolve_selection(args.provider, args.model)
    provider_name, model = selection.route, selection.model
    if provider_name not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported planner provider: {provider_name}")
    # A pinned model the gateway no longer offers used to fail every turn
    # with a raw HTTP 404 (live test 2026-09-03, S38). Ask the gateway once
    # per session, swap to an offered menu sibling and say so; an explicit
    # --model is the user's call and is left alone.
    if not (args.model or "").strip():
        model = _reconcile_model_with_gateway(provider_name, model)
    # Both API-key providers require an explicit model; config supplies
    # per-provider defaults (openai.model=gpt-5.5,
    # anthropic.model=claude-sonnet-4-6) so this only trips when the user
    # has cleared the default.
    if not model:
        raise ValueError(
            f"No model configured for provider {provider_name!r}. Run "
            f"`aiswmm model --provider {provider_name} --model <model-id>`."
        )

    # PRD-X: bind a per-process MCP pool so list_tools / call_tool against
    # local servers reuse one long-running node child per server instead of
    # spawning on every call. Lazy — pool only spawns servers on first use.
    ensure_session_pool()

    # Provider-neutral construction: every backend is built through the
    # factory so adding a new provider is a factory-only change. Tests
    # patch ``runtime_loop.make_provider`` (or the factory) to inject a
    # stub provider.
    provider = make_provider(provider_name, model=model)

    # One compact context line instead of the old four-line preamble
    # ("aiswmm executor" carried no information; planner and evidence
    # folder now share a line — the turn's tail still prints the final
    # report path when there is something to read).
    # The continuation block is for the planner, not for the person
    # watching. Echoing it put ten lines of internal plumbing on screen
    # every time a turn continued the previous one.
    _agent_say(f"Goal: {display_goal(goal)}")
    _agent_say(
        f"Session: {provider_name} ({model}) → {_display_path(session_dir)}"
    )
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
    # Single-shot sessions end when the model's final text is printed:
    # the user CANNOT reply. Without this block the planner obeyed the
    # base prompt's "ask the user to choose" directives and ended
    # sessions with unanswerable questions (found 2026-08-09 NL sweep:
    # plot flow asked node/attr, canada flow asked for a bbox, both
    # after the session was already over). Interactive turns keep the
    # ask-first behavior — there a question is answerable.
    if not bool(getattr(args, "interactive", False)):
        extras = [
            (
                "<session-mode>single-shot: the user cannot reply to your "
                "final message, so never end with a question. When a choice "
                "has a clear recommended default (e.g. the recommended plot "
                "node, Total_inflow, the full simulation window), take the "
                "default and state in the result card which default you "
                "chose and why. Only stop without acting when a REQUIRED "
                "input is genuinely missing and has no safe default (e.g. "
                "no AOI and no documented demo AOI); then say exactly what "
                "to rerun with, phrased as an instruction, not a "
                "question.</session-mode>"
            ),
            *extras,
        ]
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
        provider_route=provider_name,
    )
    if outcome_box is not None:
        # Carry the final text back to the REPL so it can record the
        # pending-question state for the next turn (BUG-1 fix).
        outcome_box.append(outcome.final_text or "")

    if chat_session:
        # Persist a minimal session_state.json so the chat-note generator
        # has structured context to work with.
        state_path = agent_file_for_write(session_dir, "session_state.json")
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
        # Live finding F-73 (2026-09-02): a chat turn printed no "LLM usage"
        # line, so the cost of a question was invisible while a failed run
        # turn showed it. The summary renders the usage line alone when the
        # session produced no manifest.
        summary_block = render_final_summary([session_dir])
        if summary_block:
            print(summary_block)
        return _exit_code_for(outcome)

    report = _write_report(
        session_dir,
        goal,
        outcome.plan,
        outcome.results,
        dry_run=args.dry_run,
        allowed_tools=registry.names,
        planner="llm",
        final_text=outcome.final_text,
    )
    _write_event(trace_path, {"event": "session_end", "ok": outcome.ok, "report": str(report), "final_text": outcome.final_text})
    _sync_session_end(session_dir)
    _refresh_moc_after_session(session_dir)
    # Honesty in the announcement (BUG-7, user live test 2026-08-09):
    # a turn that only ASKED something executed no consequential tool,
    # and calling its artifact a "Final report" implied a completed
    # run. The file is the same turn record either way; the label now
    # matches what actually happened.
    consequential = any(
        not registry.is_read_only(getattr(call, "name", ""))
        for call in outcome.plan
    )
    label = "Final report" if consequential else "Clarification note"
    _agent_say(f"{label}: {_display_path(report)}")
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
    return _exit_code_for(outcome)


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


@on_exception_return_default(default=None, scope="session_db_sync_end")
def _sync_session_end(session_dir: Path) -> None:
    """Sync the just-finished session into the SQLite store.

    Idempotent and silent: per-session double-call is a cheap no-op
    thanks to the unique indices, and any IO failure is swallowed so
    the user's turn return code is unaffected. The
    ``@on_exception_return_default`` boundary (issue #207) preserves
    the swallow-and-return-None contract and surfaces failures under
    ``scope="session_db_sync_end"`` in ``silent_fallbacks.jsonl``. The
    ``finally`` block still runs because Python evaluates it *inside*
    the wrapped function before the exception propagates to the
    decorator, so ``_SYNCED_SESSION_DIRS`` is updated on both the
    happy and error paths exactly as before.
    """
    key = str(session_dir.resolve()) if session_dir else ""
    if not key or key in _SYNCED_SESSION_DIRS:
        return
    try:
        sync_session_to_db(session_dir)
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


@on_exception_return_default(default=None, scope="session_db_sync_atexit")
def _atexit_sync_one(raw: str) -> None:
    """Sync one session_dir at atexit; never abort the surrounding loop.

    Extracted so the broad ``Exception`` catch lives at the
    per-session boundary rather than ad-hoc inside the loop. The
    ``@on_exception_return_default`` boundary (issue #207) lets the
    outer iterator advance to the next session when one sync raises,
    matching the legacy ``except Exception: continue`` semantics.
    """
    sync_session_to_db(Path(raw))


def _atexit_sync_recent_sessions() -> None:
    """Belt-and-suspenders: re-sync any session we already touched.

    The end-of-session hook is the primary write path. This atexit
    handler exists for the crash case — process exits before the hook
    fires (Ctrl-C, OOM kill, etc.). It walks every session dir we have
    seen in this process and runs the projector again; the unique
    indices guarantee idempotency.
    """
    for raw in list(_SYNCED_SESSION_DIRS):
        _atexit_sync_one(raw)


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
        # The shell can be driven through a pipe, where approval fails
        # closed. Surface the same guidance the one-shot path gives so a
        # refusal is never a dead end in either surface.
        hint = result.get("hint")
        if hint:
            print(_chrome.err(hint), file=out, flush=True)
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
