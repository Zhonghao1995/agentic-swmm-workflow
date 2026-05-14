"""Stdlib ANSI helpers for the agent UI.

PRD_runtime: zero new dependencies. Helpers degrade to plain text when
the ``NO_COLOR`` environment variable is set or when ``stdout`` is not
a TTY. Importers should use ``colorize(text, color)`` rather than
concatenating raw escape sequences so the degradation rule is enforced
in one place.
"""

from __future__ import annotations

import os
import sys

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
FG_BLUE = "\033[34m"
FG_GREEN = "\033[32m"
FG_RED = "\033[31m"
FG_YELLOW = "\033[33m"


def supports_color() -> bool:
    """Return True iff ANSI colours should actually be emitted."""
    if os.environ.get("NO_COLOR") is not None:
        # https://no-color.org/ — any value disables colours.
        return False
    stream = sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def colorize(text: str, color: str) -> str:
    """Wrap ``text`` in ``color`` ANSI escapes when colour is enabled."""
    if not text or not color or not supports_color():
        return text
    return f"{color}{text}{RESET}"
