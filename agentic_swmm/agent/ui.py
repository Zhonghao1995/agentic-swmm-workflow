from __future__ import annotations

import enum
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any

from agentic_swmm.agent import ui_colors
from agentic_swmm.utils.paths import repo_root


_PROMPT = "aiswmm>"
_INDENT = " " * (len(_PROMPT) + 1)  # match "aiswmm> " spacing


def agent_say(text: str) -> None:
    """Print an agent line. Only the first non-empty line gets the
    ``aiswmm>`` prefix; subsequent lines are indented to align with it
    (PRD_runtime user story 7).
    """
    if not text:
        print(_styled_prompt())
        return
    lines = text.splitlines() or [text]
    first = True
    for line in lines:
        if not line:
            # Preserve blank lines as a bare indent so paragraphs hold
            # together visually.
            print(_INDENT.rstrip())
            continue
        if first:
            print(f"{_styled_prompt()} {line}")
            first = False
        else:
            print(f"{_INDENT}{line}")


def _styled_prompt() -> str:
    return ui_colors.colorize(_PROMPT, ui_colors.FG_BLUE)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path)


def compact_plan(plan: list[Any]) -> str:
    if not plan:
        return "no tool calls"
    return " -> ".join(str(call.name) for call in plan)


# ---------------------------------------------------------------------------
# Spinner (PRD_runtime user story 8 + PRD_product_ux_overhaul UX-3)
# ---------------------------------------------------------------------------


class SpinnerState(enum.Enum):
    """High-level states a ``Spinner`` can advertise.

    Issue #58 (UX-3): the spinner gained a state enum so it can be
    reused beyond per-tool progress. ``THINKING`` covers the silent
    5-30s window inside ``OpenAIPlanner.run`` while we wait on the
    LLM; ``RUNNING`` is the existing per-tool behaviour; ``WAITING``
    is reserved for future user-confirmation pauses; ``DONE`` and
    ``FAILED`` are the terminal markers when a Spinner finishes.
    """

    THINKING = "thinking"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


class Spinner:
    """Single-line carriage-return spinner for tool / LLM progress.

    On a TTY ``stdout`` the spinner overwrites the previous line with
    ``\\r`` so 10 tool calls do not produce 20 scroll lines. On a
    non-TTY stream (CI logs, captured stdout, redirected files) it
    falls back to one newline-terminated line per ``update`` /
    ``finish`` so existing log scrapers stay readable.

    For ``state=SpinnerState.THINKING`` (issue #58), the spinner runs
    a background ticker that advances the frame every ~120 ms so the
    user sees motion while the planner blocks on the LLM. On non-TTY
    the ticker is a no-op — only the entry line is emitted.

    Usage::

        with Spinner("plot_run") as spinner:
            ...
            spinner.update("inspect_plot_options")

        with Spinner("Thinking…", state=SpinnerState.THINKING):
            response = provider.respond_with_tools(...)
    """

    _FRAMES = ("[/]", "[\\]", "[|]", "[-]")
    _TICK_SECONDS = 0.12

    def __init__(
        self,
        label: str,
        stream: IO[str] | None = None,
        *,
        state: SpinnerState = SpinnerState.RUNNING,
    ) -> None:
        self.label = label
        self.state = state
        self.stream = stream if stream is not None else sys.stdout
        self._frame = 0
        self._is_tty = self._stream_is_tty(self.stream)
        self._closed = False
        # Background ticker — only used for THINKING on a TTY.
        self._stop_event: threading.Event | None = None
        self._ticker: threading.Thread | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> "Spinner":
        self._render()
        if self.state is SpinnerState.THINKING and self._is_tty:
            self._start_ticker()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish()

    def update(self, label: str) -> None:
        with self._lock:
            self.label = label
            self._frame = (self._frame + 1) % len(self._FRAMES)
            self._render()

    def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_ticker()
        if self._is_tty:
            # Terminate the overwritten line so the next print starts
            # cleanly on a new row.
            try:
                self.stream.write("\n")
                self.stream.flush()
            except Exception:  # pragma: no cover - best effort
                pass

    def _render(self) -> None:
        if self._closed:
            return
        frame = self._FRAMES[self._frame]
        if self._is_tty:
            line = f"\r{frame} {self.label}"
        else:
            line = f"{frame} {self.label}\n"
        try:
            self.stream.write(line)
            self.stream.flush()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- ticker (THINKING only) --------------------------------------------

    def _start_ticker(self) -> None:
        self._stop_event = threading.Event()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()

    def _stop_ticker(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._ticker is not None:
            self._ticker.join(timeout=1.0)
        self._stop_event = None
        self._ticker = None

    def _tick_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.wait(self._TICK_SECONDS):
            with self._lock:
                if self._closed:
                    return
                self._frame = (self._frame + 1) % len(self._FRAMES)
                self._render()

    @staticmethod
    def _stream_is_tty(stream: IO[str]) -> bool:
        isatty = getattr(stream, "isatty", None)
        if isatty is None:
            return False
        try:
            return bool(isatty())
        except (AttributeError, ValueError):
            return False
