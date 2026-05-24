"""Reproduction + regression test for issue #184 — spinner residue.

Bug (issue #184): each ``Spinner(THINKING)`` finish leaves an orphan
``[\\] Thinking…`` line on screen. After 10 tool calls the user sees
10 piled-up residue lines between the step rows.

Expected: a Spinner on a TTY must wipe its own line on finish so the
subsequent step-row print starts on a clean line. The next THINKING
spinner is the only ``Thinking…`` visible on screen.

This test drives two consecutive ``Spinner(THINKING)`` → step-row
prints on a TTY-emulated stream, renders the captured stdout the way
a real terminal would (applying ``\\r``, ``\\x1b[2K`` and ``\\n``),
and asserts the visible content contains exactly ONE ``Thinking``
substring (the still-active second spinner) — not two, not zero.

Failing on current ``main`` is the point: ``Spinner.finish`` writes
``\\n`` after the last frame, freezing the residual ``[\\] Thinking…``
into the scrollback.
"""
from __future__ import annotations

import io
import re
import unittest

from conftest import _FakeTTYStream

from agentic_swmm.agent.ui import Spinner, SpinnerState


_ANSI_CSI_RE = re.compile(r"\x1b\[(\d*)([A-Za-z])")


def _render_terminal(raw: str) -> str:
    """Apply terminal semantics to a captured stdout buffer.

    Implements just enough of a VT100 to model the spinner's behaviour:
      * ``\\r`` returns the cursor to column 0 of the current line
        (does NOT clear the line — subsequent chars overwrite).
      * ``\\x1b[2K`` clears the entire current line and leaves the
        cursor where it was (we treat as wipe-to-end-of-line; combined
        with the preceding ``\\r`` that yields a fully blanked line).
      * ``\\n`` advances to a new line, cursor column 0.
      * Any other printable char overwrites the cell at the current
        column, padding the line with spaces if we jumped past the
        previous end.

    Returns the final on-screen contents joined by ``\\n``.
    """
    lines: list[list[str]] = [[]]
    col = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\r":
            col = 0
            i += 1
            continue
        if ch == "\n":
            lines.append([])
            col = 0
            i += 1
            continue
        if ch == "\x1b":
            m = _ANSI_CSI_RE.match(raw, i)
            if m:
                param, final = m.group(1), m.group(2)
                if final == "K":
                    # Erase in line: 0/empty=cursor to EOL, 2=entire line.
                    if param in ("", "0"):
                        del lines[-1][col:]
                    elif param == "1":
                        for c in range(min(col, len(lines[-1]))):
                            lines[-1][c] = " "
                    elif param == "2":
                        lines[-1] = []
                        col = 0
                    i = m.end()
                    continue
                # Unhandled CSI — drop the sequence rather than render.
                i = m.end()
                continue
            # Lone ESC — skip it.
            i += 1
            continue
        # Regular char: overwrite cell at ``col``, padding with spaces.
        while len(lines[-1]) < col:
            lines[-1].append(" ")
        if col < len(lines[-1]):
            lines[-1][col] = ch
        else:
            lines[-1].append(ch)
        col += 1
        i += 1
    return "\n".join("".join(line).rstrip() for line in lines)


class TerminalRendererSelfTests(unittest.TestCase):
    """Sanity check the renderer before we rely on it for the bug test."""

    def test_carriage_return_overwrites(self) -> None:
        self.assertEqual(_render_terminal("hello\rworld"), "world")

    def test_clear_eol_blanks_line(self) -> None:
        # CR + clear-EOL wipes the line; subsequent print lands on the
        # cleared line.
        self.assertEqual(_render_terminal("hello\r\x1b[2Kbye\n"), "bye\n")

    def test_newline_advances(self) -> None:
        self.assertEqual(_render_terminal("a\nb"), "a\nb")

    def test_short_overwrite_leaves_trailing_chars(self) -> None:
        # Without explicit clear, CR + short text leaves the tail of
        # the original visible (classic spinner residue).
        # rstrip handles trailing spaces; the trailing chars survive.
        self.assertEqual(_render_terminal("Thinking\rOK\n"), "OKinking\n")


class SpinnerNonTTYEscapeFreeTests(unittest.TestCase):
    """Regression guard for the non-TTY path.

    The fix for issue #184 introduces ANSI escape sequences
    (``\\r\\x1b[2K``) on the TTY ``finish`` path. The non-TTY path
    MUST stay escape-free so CI logs, redirected files, and grep
    pipelines see plain text only. (This locks the existing
    contract — both pre-fix and post-fix the non-TTY path emits
    no escapes — so future edits cannot leak the TTY behaviour
    into log files by accident.)
    """

    def test_non_tty_spinner_has_no_ansi_escape_sequences(self) -> None:
        # Plain StringIO — its ``isatty()`` returns False by default.
        stream = io.StringIO()
        # Drive the THINKING path (which on a TTY runs a background
        # ticker that emits frames every ~120 ms) and exercise
        # update() too so every spinner code path that writes runs
        # at least once.
        with Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING) as spinner:
            spinner.update("step 1")
            spinner.update("step 2")
        output = stream.getvalue()
        self.assertNotIn(
            "\x1b",
            output,
            "non-TTY spinner output must contain zero ANSI escape "
            f"bytes; got {output!r}",
        )
        # Belt-and-braces: also assert the CSI prefix ``\x1b[`` is
        # absent — same intent, more specific match per PRD wording.
        self.assertNotIn(
            "\x1b[",
            output,
            "non-TTY spinner output must contain no CSI sequences; "
            f"got {output!r}",
        )


class SpinnerResidueIssue184Tests(unittest.TestCase):
    def test_two_thinking_spinners_leave_no_residue_between_step_rows(self) -> None:
        """Two consecutive THINKING spinners followed by step rows
        must leave exactly ONE ``Thinking`` visible on screen — the
        still-active second spinner. Pre-fix, the first spinner's
        last frame remains as an orphan line.
        """
        stream = _FakeTTYStream()

        # ---- Step 1 ----
        # THINKING spinner: enter (no ticker — we don't sleep in this
        # test so no auto-tick fires) then exit. This mimics the
        # planner's ``with Spinner("Thinking…", state=THINKING):``
        # block around ``provider.respond_with_tools``.
        with Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING):
            pass
        # Then the planner emits ``[N] toolname`` via _agent_say which
        # ultimately ``print``s to stdout. We simulate that line
        # directly on the same stream so the test models the full
        # transition.
        stream.write("aiswmm> [17] run_swmm_inp\n")

        # ---- Step 2 ----
        with Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING):
            pass
        stream.write("aiswmm> [18] select_skill\n")

        # ---- Step 3: another THINKING spinner that is still active
        # (we do NOT close it inside this block — we end with one
        # active spinner on screen).
        spinner = Spinner("Thinking…", stream=stream, state=SpinnerState.THINKING)
        spinner.__enter__()
        try:
            raw = stream.getvalue()
            visible = _render_terminal(raw)
        finally:
            spinner.finish()

        # Count occurrences of ``Thinking`` in the rendered on-screen
        # content. Pre-fix this is 3 (one orphan per finished spinner
        # + one for the still-active spinner). Post-fix this is 1
        # (only the still-active spinner is on screen).
        thinking_count = visible.count("Thinking")
        self.assertEqual(
            thinking_count,
            1,
            "Visible terminal must show exactly ONE 'Thinking' line "
            "(the still-active spinner); orphan residue from "
            "finished spinners is the bug.\n"
            f"raw stream = {raw!r}\n"
            f"visible    = {visible!r}\n"
            f"count      = {thinking_count}",
        )


if __name__ == "__main__":
    unittest.main()
