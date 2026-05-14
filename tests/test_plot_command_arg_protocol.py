"""Regression test for the commands/plot.py vs plot_rain_runoff_si.py
argument-protocol drift.

Background: `commands/plot.py` used to unconditionally append
`--auto-window-mode` and `--window-hours` to the subprocess command that
runs `skills/swmm-plot/scripts/plot_rain_runoff_si.py`. The target
script never had these arguments, so every `plot_run` tool call failed
with `argparse: unrecognized arguments`. A real session
(`runs/2026-05-13/224244_tecnopolo_run/`) is the motivating evidence.

These tests pin the fix: neither the CLI nor the subprocess command
should mention those two flags. `--pad-hours` is genuine and stays.
"""

from __future__ import annotations

import inspect

from agentic_swmm.commands import plot


def test_plot_main_does_not_pass_unsupported_args_to_script() -> None:
    src = inspect.getsource(plot.main)
    assert "--auto-window-mode" not in src, (
        "commands/plot.py:main must not forward --auto-window-mode; "
        "plot_rain_runoff_si.py does not accept it (verified by "
        "git log -S 'auto-window-mode' on the script — never existed)."
    )
    assert "--window-hours" not in src, (
        "commands/plot.py:main must not forward --window-hours; "
        "plot_rain_runoff_si.py does not accept it."
    )
    # Negative-control: --pad-hours IS supported by the script and should remain.
    assert "--pad-hours" in src, (
        "commands/plot.py:main is expected to forward --pad-hours "
        "(the target script accepts it)."
    )


def test_plot_cli_parser_does_not_advertise_unimplemented_flags() -> None:
    """If the CLI doesn't pass the flag, it shouldn't advertise it either —
    otherwise the LLM planner sees an `--auto-window-mode` option in
    `plot --help` and gets misled."""
    src = inspect.getsource(plot)
    # The argparse add_argument lines for these flags must be removed.
    for needle in ("--auto-window-mode", "--window-hours"):
        # Allow the strings to live in *comments* (e.g. TODO referencing
        # the future re-introduction), but not in `add_argument(` calls.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert needle not in line or "add_argument" not in line, (
                f"commands/plot.py CLI must not register {needle} via "
                f"add_argument until the target script implements it. "
                f"Offending line: {line!r}"
            )
