"""Interactive REPL loop (PRD-02).

``run_repl`` is the input → command-dispatch → planner-invocation
state machine that used to live inside ``runtime_loop.run_interactive_shell``.
The split extracts the loop with constructor-injected collaborators
(input source, planner runner, output sink) so the REPL is testable
without a real terminal or OpenAI provider.

The function intentionally takes the ``argparse.Namespace`` opaquely
and forwards it to ``planner_runner``: the planner-side concerns
(provider name, model, dry-run, verbose, max-steps) belong to that
collaborator, not to the loop. The loop only consumes
``args.session_dir`` and ``args.planner`` directly (via the boot
guard that callers usually run before instantiating us — kept here as
a defensive check so a wrong-planner path still fails loudly).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Protocol

from agentic_swmm.agent.warm_intro import WarmIntroState, maybe_emit_warm_intro

#: Live finding F-63 (2026-09-02): a declined tool call is not a failed
#: turn. The runtime returns this code when the turn did not succeed and
#: at least one prompted tool was declined, so the shell can say so and
#: scripts can tell the two apart.
DECLINED_EXIT_CODE = 3
from agentic_swmm.agent.swmm_runtime.run_layout import agent_file, agent_file_for_write

__all__ = ["run_repl", "PlannerRunner"]


class PlannerRunner(Protocol):
    """Callable shape of ``runtime_loop.run_openai_planner``."""

    def __call__(
        self,
        args: argparse.Namespace,
        goal: str,
        session_dir: Path,
        trace_path: Path,
        registry: Any,
        *,
        chat_session: bool = False,
        prior_session_state: dict[str, Any] | None = None,
    ) -> int: ...


_EXIT_COMMANDS = frozenset({"/exit", "/quit", "exit", "quit"})
_NEW_SESSION_COMMANDS = frozenset({"/new-session", "/new session", "new session"})
# Bare-word help only (matched against the whole stripped line) so "help me
# run X" stays a real goal. The welcome banner advertises help, so it must be
# answered locally — never billed to the planner.
_HELP_COMMANDS = frozenset({"/help", "help", "/commands", "?"})
_HELP_TEXT = (
    "Commands:\n"
    "  /help          show this help\n"
    "  /new-session   start a fresh session (keeps the runtime running)\n"
    "  /exit          quit (or press Ctrl-D)\n"
    "\n"
    "Anything else you type is sent to the agent as a modelling request,\n"
    "e.g. \"run my model and audit it\". Start the runtime with --safe to\n"
    "confirm every tool call before it runs."
)


def run_repl(
    args: argparse.Namespace,
    *,
    base_dir: Path,
    profile_name: str,
    input_source: Callable[[str], str],
    planner_runner: PlannerRunner,
    output: Callable[[str], None],
    on_new_session: Callable[[], None] | None = None,
) -> int:
    """Run the interactive REPL until the user exits or EOF.

    Collaborators:

    - ``input_source(prompt: str) -> str``: produces the next user
      line. Raises ``EOFError`` to end the session cleanly.
    - ``planner_runner``: runs one planner turn for a user goal
      (typically ``runtime_loop.run_openai_planner``).
    - ``output(text)``: emits a single line to the user.
    - ``on_new_session``: optional hook the REPL invokes when the
      user types ``/new-session``. The boot-time facade uses this
      to rebind its per-session ``date_dir`` and emit the
      ``New session: <label>`` confirmation; tests typically pass
      ``None``.

    Returns the integer exit code (always 0 today; reserved for
    fatal-error paths in the future).
    """
    state = WarmIntroState()
    first_prompt_pending = True
    while True:
        try:
            prompt = input_source("you> ").strip()
        except EOFError:
            return 0
        if prompt in _EXIT_COMMANDS:
            return 0
        if prompt in _NEW_SESSION_COMMANDS:
            state = WarmIntroState()
            first_prompt_pending = True
            if on_new_session is not None:
                on_new_session()
            else:
                output("New session ready.")
            continue
        if prompt in _HELP_COMMANDS:
            output(_HELP_TEXT)
            continue
        if not prompt:
            continue

        # Warm intro is eligible ONLY for the very first prompt of a
        # session. It used to stay armed until an open-shaped prompt
        # came along, so a mid-conversation short reply (a bare bbox
        # answering the assistant's own question) was greeted with
        # "Hi! I'm Agentic SWMM..." and the answer was lost (BUG-2,
        # user live test 2026-08-09).
        if first_prompt_pending:
            first_prompt_pending = False
            intro_text = maybe_emit_warm_intro(state, prompt)
            if intro_text is not None:
                output(intro_text)
                continue

        # Per-turn dispatch — keep the loop testable by delegating all
        # of the per-turn filesystem layout + planner choice to the
        # injected runner. The loop's only contribution is the goal
        # string and a placeholder session directory; downstream
        # collaborators decide chat-vs-run.
        session_dir = base_dir
        # Placeholder, as the comment above says: the real session directory is
        # chosen per turn downstream. Resolve without creating, or the runs
        # base collects an empty _agent/ that nothing ever writes to.
        trace_path = agent_file(session_dir, "agent_trace.jsonl")
        try:
            rc = planner_runner(
                args,
                prompt,
                session_dir,
                trace_path,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - the shell must outlive one bad turn
            # A provider error (gateway 404 model_not_found, connection
            # refused, missing credentials) used to escape the loop and
            # the whole interactive session died with exit 1 (live test
            # 2026-09-03, S38). One-shot mode keeps its top-level handler;
            # inside the shell the user gets the prompt back to retry,
            # change the route, or leave.
            output(f"error: {exc}")
            output(
                "The turn did not run. Check the provider (aiswmm doctor, aiswmm setup), "
                "then ask again, or type /exit."
            )
            continue
        if rc == DECLINED_EXIT_CODE:
            output("Turn ended: you declined a tool call, so nothing ran. Ask again when ready, or type /exit.")
        elif rc != 0:
            output(f"Turn failed with exit code {rc}. You can continue or type /exit.")
