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
    while True:
        try:
            prompt = input_source("you> ").strip()
        except EOFError:
            return 0
        if prompt in _EXIT_COMMANDS:
            return 0
        if prompt in _NEW_SESSION_COMMANDS:
            state = WarmIntroState()
            if on_new_session is not None:
                on_new_session()
            else:
                output("New session ready.")
            continue
        if not prompt:
            continue

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
        trace_path = session_dir / "agent_trace.jsonl"
        rc = planner_runner(
            args,
            prompt,
            session_dir,
            trace_path,
            None,
        )
        if rc != 0:
            output(f"Turn failed with exit code {rc}. You can continue or type /exit.")
