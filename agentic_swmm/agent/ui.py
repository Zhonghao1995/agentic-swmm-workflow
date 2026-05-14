from __future__ import annotations

import sys
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
# Spinner (PRD_runtime user story 8)
# ---------------------------------------------------------------------------


class Spinner:
    """Single-line carriage-return spinner for tool progress.

    On a TTY ``stdout`` the spinner overwrites the previous line with
    ``\\r`` so 10 tool calls do not produce 20 scroll lines. On a
    non-TTY stream (CI logs, captured stdout, redirected files) it
    falls back to one newline-terminated line per ``update`` /
    ``finish`` so existing log scrapers stay readable.

    Usage::

        with Spinner("plot_run") as spinner:
            ...
            spinner.update("inspect_plot_options")
    """

    _FRAMES = ("[/]", "[\\]", "[|]", "[-]")

    def __init__(self, label: str, stream: IO[str] | None = None) -> None:
        self.label = label
        self.stream = stream if stream is not None else sys.stdout
        self._frame = 0
        self._is_tty = self._stream_is_tty(self.stream)
        self._closed = False

    def __enter__(self) -> "Spinner":
        self._render()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish()

    def update(self, label: str) -> None:
        self.label = label
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self._render()

    def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
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

    @staticmethod
    def _stream_is_tty(stream: IO[str]) -> bool:
        isatty = getattr(stream, "isatty", None)
        if isatty is None:
            return False
        try:
            return bool(isatty())
        except (AttributeError, ValueError):
            return False
