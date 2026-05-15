"""Single source of truth for retro-CRT chrome (PRD-TUI-REDESIGN).

Every ANSI escape, every box-drawing character, and every ``[SYS]``-style
prefix string used by the aiswmm runtime funnels through this module.
Other modules import the public helpers (``sys``, ``inf``, ``err``,
``wrn``, ``frame``) and never concatenate escape codes by hand.

Opt-out env vars (both honoured by every helper):

- ``NO_COLOR`` (industry standard, https://no-color.org/) — strip ANSI
  colour escapes but preserve prefixes and frame characters.
- ``AISWMM_TUI=plain`` (aiswmm-specific, more aggressive) — strip
  colour, prefixes, AND frame characters. Pure ASCII output suitable
  for paper figures and copy-paste into static documents. Default is
  ``AISWMM_TUI=retro`` (full chrome).

When both are set, ``AISWMM_TUI=plain`` wins (strictest).

When ``sys.stdout`` is not a TTY (pipe / file redirect / CI log),
colour is stripped automatically but prefixes and frames are kept — a
CI log still wants the ``[ERR]`` marker; it just doesn't want the
escape codes.

The module imports the real :mod:`sys` as ``_sys`` because it exports a
public function named ``sys()`` (matching the ``[SYS]`` chrome prefix).
"""

from __future__ import annotations

import os
import sys as _sys
from typing import Iterable

# 256-colour ANSI escapes. Preferred over 24-bit true-colour because
# every modern terminal + SSH client implements the 256-colour palette;
# true-colour support is patchy on tmux + old Linux ttys.
PHOSPHOR_GREEN = "\033[38;5;46m"
PHOSPHOR_DIM = "\033[38;5;28m"
WARN_AMBER = "\033[38;5;214m"
ERROR_RED = "\033[38;5;196m"
RESET = "\033[0m"

# Box-drawing: Light edges + Rounded corners. No heavy / double /
# cross junctions — frames are simple rectangles, not nested tables.
H_LIGHT = "─"
V_LIGHT = "│"
CORNER_TL = "╭"  # top-left
CORNER_TR = "╮"  # top-right
CORNER_BL = "╰"  # bottom-left
CORNER_BR = "╯"  # bottom-right


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


def is_plain() -> bool:
    """Return True iff ``AISWMM_TUI=plain`` is set.

    Plain mode strips colour AND prefixes AND frame characters. The
    default ``AISWMM_TUI=retro`` keeps full chrome.
    """
    return os.environ.get("AISWMM_TUI", "retro").lower() == "plain"


def use_colour() -> bool:
    """Return True iff ANSI escape sequences should be emitted.

    Three "no" answers, in priority order:

    1. ``AISWMM_TUI=plain`` — strict plain output.
    2. ``NO_COLOR`` set (any value) — industry-standard opt-out.
    3. ``sys.stdout.isatty()`` is False — redirected to a pipe or file.
    """
    if is_plain():
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    isatty = getattr(_sys.stdout, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (AttributeError, ValueError):
        return False


def use_chrome() -> bool:
    """Return True iff prefixes and frame characters should be emitted.

    Only ``AISWMM_TUI=plain`` strips chrome — ``NO_COLOR`` does not.
    The no-color spec is explicitly about colour, not about removing
    every visual scaffold.
    """
    return not is_plain()


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _colourize(code: str, text: str) -> str:
    """Wrap ``text`` in ``code`` + RESET when colour is active."""
    if not use_colour():
        return text
    return f"{code}{text}{RESET}"


def phosphor_green(text: str) -> str:
    """Bright phosphor green (#00ff00 — primary chrome colour)."""
    return _colourize(PHOSPHOR_GREEN, text)


def phosphor_dim(text: str) -> str:
    """Dim phosphor green (#009900 — de-emphasised chrome)."""
    return _colourize(PHOSPHOR_DIM, text)


def warn_amber(text: str) -> str:
    """Warn amber (#ffb000 — warning severity)."""
    return _colourize(WARN_AMBER, text)


def error_red(text: str) -> str:
    """Error red (#ff3333 — error severity)."""
    return _colourize(ERROR_RED, text)


# ---------------------------------------------------------------------------
# Prefix builders
# ---------------------------------------------------------------------------


def _prefixed(tag: str, msg: str, colour_fn) -> str:
    """Build ``[TAG] msg`` with the given colour, or strip in plain mode.

    Plain mode returns ``msg`` bare — no prefix, no colour. This is the
    strictest opt-out: paper authors and screen readers see literal
    text without scaffolding.
    """
    if not use_chrome():
        return msg
    return colour_fn(f"[{tag}] {msg}")


def sys(msg: str) -> str:
    """``[SYS] msg`` in phosphor green (system / chrome status)."""
    return _prefixed("SYS", msg, phosphor_green)


def inf(msg: str) -> str:
    """``[INF] msg`` in phosphor green (informational success)."""
    return _prefixed("INF", msg, phosphor_green)


def err(msg: str) -> str:
    """``[ERR] msg`` in error red (failure that should stop the user)."""
    return _prefixed("ERR", msg, error_red)


def wrn(msg: str) -> str:
    """``[WRN] msg`` in warn amber (advisory — the user should know)."""
    return _prefixed("WRN", msg, warn_amber)


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------


_CHROME_TAG_PREFIX_LEN = len("[XXX] ")


def _strip_chrome_tag(title: str) -> str:
    """Strip a leading ``[XXX] `` chrome tag from ``title``.

    Used by ``frame()`` under plain mode so a title like
    ``[SYS] aiswmm ONLINE`` collapses to ``aiswmm ONLINE``. Recognises
    any of the four canonical tags (``SYS`` / ``INF`` / ``ERR`` /
    ``WRN``); leaves unrecognised brackets alone so user content
    survives.
    """
    for tag in ("[SYS] ", "[INF] ", "[ERR] ", "[WRN] "):
        if title.startswith(tag):
            return title[_CHROME_TAG_PREFIX_LEN:]
    return title


def frame(title: str, lines: Iterable[str], *, width: int | None = None) -> str:
    """Render a rounded-corner, light-edge frame around ``lines``.

    Layout (retro mode)::

        ╭─ TITLE ──────────────────────╮
        │ line1                        │
        │ line2                        │
        ╰──────────────────────────────╯

    Plain mode (``AISWMM_TUI=plain``) collapses to::

        == TITLE ==
        line1
        line2

    no box-drawing characters at all, suitable for paper screenshots.

    Width auto-fits the longest of ``title`` or any line. Pass
    ``width`` to force a specific inner width (longer lines are NOT
    truncated — the frame just grows; this avoids surprising data
    loss).
    """
    line_list = list(lines)
    if not use_chrome():
        # Strip any literal ``[TAG] `` prefix that callers tucked into
        # the title — plain mode promises the strict opt-out, and a
        # surviving ``[SYS]`` would defeat that promise for paper
        # screenshots.
        plain_title = _strip_chrome_tag(title)
        body = [f"== {plain_title} ==", *line_list, ""]
        return "\n".join(body)

    # Total row width. Three constraints, take the max:
    #   - Title row: "╭─ TITLE ─╮" needs len(title) + 6 chars minimum.
    #   - Body row:  "│ line │"   needs len(line) + 4 chars minimum.
    #   - Caller's explicit ``width`` (if any).
    longest_line = max((len(line) for line in line_list), default=0)
    total_width = max(len(title) + 6, longest_line + 4, width or 0)

    # title_padding is the run of ``─`` after ``╭─ TITLE `` and before
    # the closing ``╮``. Total row = ╭(1) + ─(1) + space(1) + title +
    # space(1) + ─*pad + ╮(1) = title + 5 + pad.
    title_padding = total_width - len(title) - 5
    if title_padding < 1:
        title_padding = 1
        total_width = len(title) + 5 + title_padding
    top = f"{CORNER_TL}{H_LIGHT} {title} {H_LIGHT * title_padding}{CORNER_TR}"
    # Body row: │(1) + space(1) + content + space(1) + │(1) = total.
    # Content width = total - 4.
    content_width = total_width - 4
    body_lines = [
        f"{V_LIGHT} {line.ljust(content_width)} {V_LIGHT}" for line in line_list
    ]
    # Bottom row: ╰ + ─*(total-2) + ╯ = total.
    bottom = f"{CORNER_BL}{H_LIGHT * (total_width - 2)}{CORNER_BR}"
    return phosphor_green("\n".join([top, *body_lines, bottom]))


__all__ = [
    "PHOSPHOR_GREEN",
    "PHOSPHOR_DIM",
    "WARN_AMBER",
    "ERROR_RED",
    "RESET",
    "H_LIGHT",
    "V_LIGHT",
    "CORNER_TL",
    "CORNER_TR",
    "CORNER_BL",
    "CORNER_BR",
    "is_plain",
    "use_colour",
    "use_chrome",
    "phosphor_green",
    "phosphor_dim",
    "warn_amber",
    "error_red",
    "sys",
    "inf",
    "err",
    "wrn",
    "frame",
]
