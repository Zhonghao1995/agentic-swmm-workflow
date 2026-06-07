"""Bug #236 regression: windowStart/windowEnd without focusDay must raise a clear error.

Before the fix:
  - passing windowStart/windowEnd without focusDay was silently ignored by the Python
    script (the code only reads them inside `if args.focus_day:`), giving the user no
    indication the axis crop was not applied.
  - SKILL.md wrongly described them as standalone ISO timestamps.

After the fix:
  - passing windowStart/windowEnd without focusDay raises SystemExit/ValueError with
    a message that says focusDay is required.
  - passing a valid focusDay + HH:MM window still works without regression.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills" / "swmm-plot" / "scripts" / "plot_rain_runoff_si.py"
)

# Minimal fake SWMM INP with a TIMESERIES block (no real simulation needed;
# the script parses the INP directly for rainfall data).
_MINIMAL_INP = """\
[TITLE]
test

[TIMESERIES]
;;Name      Date       Time    Value
RAIN        01/01/2023 06:00   1.0
RAIN        01/01/2023 06:05   2.0
RAIN        01/01/2023 06:10   1.5

[END]
"""


def _make_fake_run(tmp: Path) -> tuple[Path, Path, Path]:
    """Write a minimal INP and dummy OUT in tmp, return (inp, out, png)."""
    inp = tmp / "model.inp"
    inp.write_text(_MINIMAL_INP, encoding="utf-8")
    # A real .out binary is needed for swmmtoolbox; the validate_window_args
    # check fires BEFORE the extract() call, so a zero-byte placeholder is
    # sufficient for the error-path tests.
    out = tmp / "model.out"
    out.write_bytes(b"\x00" * 16)
    png = tmp / "out.png"
    return inp, out, png


def _run_script(*extra_args: str, tmp: Path) -> subprocess.CompletedProcess:
    """Invoke the plot script in a subprocess and return the result."""
    inp, out, png = _make_fake_run(tmp)
    cmd = [
        sys.executable, str(_SCRIPT),
        "--inp", str(inp),
        "--out", str(out),
        "--out-png", str(png),
        "--rain-ts", "RAIN",
        "--node", "O1",
        *extra_args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class WindowContractTests(unittest.TestCase):

    def test_window_start_without_focus_day_raises_error(self) -> None:
        """windowStart without focusDay must produce a non-zero exit + clear message.

        Before the fix this was silently ignored (zero exit, axis crop skipped).
        """
        with tempfile.TemporaryDirectory() as raw:
            result = _run_script("--window-start", "08:00", "--window-end", "10:00",
                                 tmp=Path(raw))
        self.assertNotEqual(
            result.returncode, 0,
            "Expected a non-zero exit when windowStart is given without focusDay, "
            f"but script exited 0.\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        combined = (result.stdout + result.stderr).lower()
        self.assertTrue(
            "focusday" in combined or "focus_day" in combined or "focus-day" in combined,
            "Error message must mention focusDay so the user knows what is missing.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_window_end_without_focus_day_raises_error(self) -> None:
        """windowEnd alone (without focusDay) must also be rejected."""
        with tempfile.TemporaryDirectory() as raw:
            result = _run_script("--window-end", "10:00", tmp=Path(raw))
        self.assertNotEqual(result.returncode, 0,
                            "Expected non-zero exit for --window-end without --focus-day.")

    def test_focus_day_with_window_is_accepted(self) -> None:
        """A valid focusDay + HH:MM window must NOT be rejected at the validation layer.

        The script will fail later (extract() on a dummy .out), but the
        window-validation step itself must pass — exit code must not be 1
        due to the window guard. We detect this by checking that stderr does
        NOT contain the "require focusDay" message.
        """
        with tempfile.TemporaryDirectory() as raw:
            result = _run_script(
                "--focus-day", "2023-01-01",
                "--window-start", "06:00",
                "--window-end", "10:00",
                tmp=Path(raw),
            )
        combined = (result.stdout + result.stderr).lower()
        # The window validation must NOT fire; any failure here is due to the
        # dummy .out, not the window guard.
        self.assertNotIn(
            "require focusday",
            combined.replace("_", "").replace("-", ""),
            "focusDay + HH:MM window should be accepted by the validation guard.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_no_window_args_is_accepted(self) -> None:
        """Omitting both windowStart and windowEnd must not raise the validation error."""
        with tempfile.TemporaryDirectory() as raw:
            result = _run_script(tmp=Path(raw))
        combined = (result.stdout + result.stderr).lower()
        self.assertNotIn(
            "require focusday",
            combined.replace("_", "").replace("-", ""),
            "No window args should not trigger the validation guard.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
