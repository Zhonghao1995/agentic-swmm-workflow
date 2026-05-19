"""PRD-08 Phase B (audit #26): width-safe argparse formatter.

Argparse's stock formatter wraps usage lines mid-flag at 80 cols
(``--total-iters\\nTOTAL_ITERS``), which breaks copy-paste. The
``WidthSafeFormatter`` keeps each ``--flag METAVAR`` group on a
single line and only wraps at action boundaries.
"""

from __future__ import annotations

import argparse
import io
import unittest

from agentic_swmm.agent.help_router import (
    WidthSafeFormatter,
    WidthSafeRawDescriptionFormatter,
)


def _format(parser: argparse.ArgumentParser, *, columns: int = 80) -> str:
    """Render ``parser.format_help()`` at a fixed width.

    argparse honours the ``COLUMNS`` env var, but in tests we want a
    deterministic width regardless of the shell's setting. We pass a
    custom formatter via the parser to fix the width upfront.
    """

    # Force the formatter's width by monkey-patching the parser's
    # formatter factory. We mirror argparse's own approach in tests.
    formatter = parser._get_formatter()
    formatter._width = columns
    formatter.add_usage(
        parser.usage, parser._actions, parser._mutually_exclusive_groups
    )
    return formatter.format_help()


class CalibrateUsageNeverSplitsFlagsTests(unittest.TestCase):
    def _make_calibrate_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="agentic-swmm calibrate",
            formatter_class=WidthSafeFormatter,
        )
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--algorithm", choices=("sceua", "dream_zs"))
        parser.add_argument("--total-iters", type=int, required=True)
        parser.add_argument("--checkpoint-every", type=int)
        parser.add_argument("--inp", required=True)
        parser.add_argument("--observed-csv")
        parser.add_argument(
            "--param",
            action="append",
            metavar="NAME=LOW,HIGH",
            required=True,
        )
        parser.add_argument("--objective", choices=("nse", "kge", "rmse"))
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--progress", action="store_true")
        return parser

    def test_total_iters_metavar_stays_on_one_line(self) -> None:
        parser = self._make_calibrate_parser()
        text = _format(parser, columns=80)
        # If argparse wrapped mid-flag, one of the lines would end with
        # ``--total-iters`` and the next would start with
        # ``TOTAL_ITERS``. Assert that does NOT happen.
        for line in text.splitlines():
            self.assertFalse(
                line.rstrip().endswith("--total-iters"),
                f"Line wraps mid-flag: {line!r}",
            )
            self.assertFalse(
                line.lstrip().startswith("TOTAL_ITERS "),
                f"Line starts with bare METAVAR: {line!r}",
            )

    def test_param_metavar_stays_on_one_line(self) -> None:
        parser = self._make_calibrate_parser()
        text = _format(parser, columns=80)
        for line in text.splitlines():
            self.assertFalse(
                line.rstrip().endswith("--param"),
                f"--param split from its metavar: {line!r}",
            )

    def test_width_safe_raw_description_inherits_both(self) -> None:
        # The combined formatter should keep both behaviours:
        # 1. The epilog text is rendered verbatim.
        # 2. The usage line never splits a flag/metavar pair.
        parser = argparse.ArgumentParser(
            prog="agentic-swmm uncertainty",
            formatter_class=WidthSafeRawDescriptionFormatter,
            epilog="Examples:\n  aiswmm uncertainty plan --inp x.inp",
        )
        parser.add_argument("--total-iters", type=int, required=True)
        text = parser.format_help()
        self.assertIn("Examples:", text)
        # Raw mode preserves the leading two-space indent.
        self.assertIn("  aiswmm uncertainty plan --inp x.inp", text)


if __name__ == "__main__":
    unittest.main()
