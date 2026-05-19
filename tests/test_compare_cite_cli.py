"""CLI smoke tests for ``aiswmm compare`` and ``aiswmm cite`` (PRD-06 Phase B).

These are deliberately thin: confirm the subparser dispatches, that
``--json`` produces machine-readable output, and that exit codes are
correct. The business logic is covered exhaustively in
``test_compare_runs.py`` and ``test_citations.py``.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Continuity Error (%) .....        -0.171


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  Continuity Error (%) .....         0.500
"""


_CITATIONS_YAML = """\
schema_version: "1.0"

worked_example_pending_verification:
  authors: "<pending>"
  year: 0
  title: "<pending>"
  work: "<pending>"
  locator: "<pending>"
  url: ""
  verified_by: ""
  verified_on: ""
"""


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    """Build the parser, dispatch ``argv``, return ``(rc, stdout, stderr)``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


class CompareCliSmokeTests(unittest.TestCase):
    def test_compare_subcommand_registers(self) -> None:
        parser = build_parser()
        # ``compare`` should appear as a known subcommand.
        actions = [
            action
            for action in parser._actions
            if hasattr(action, "choices") and action.choices  # type: ignore[attr-defined]
        ]
        # The subparsers action carries a ``choices`` dict.
        names: set[str] = set()
        for action in actions:
            names.update(action.choices.keys())  # type: ignore[attr-defined]
        self.assertIn("compare", names)

    def test_compare_default_table_output(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_a = base / "run-a"
            run_b = base / "run-b"
            run_a.mkdir()
            run_b.mkdir()
            (run_a / "model.rpt").write_text(_HEALTHY_RPT, encoding="utf-8")
            (run_b / "model.rpt").write_text(_HEALTHY_RPT, encoding="utf-8")
            rc, out, _ = _dispatch(
                ["compare", "--run-a", str(run_a), "--run-b", str(run_b)]
            )
        self.assertEqual(rc, 0)
        self.assertIn("verdict:", out)
        self.assertIn("runoff_continuity_pct", out)

    def test_compare_json_emits_parseable_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_a = base / "ra"
            run_b = base / "rb"
            run_a.mkdir()
            run_b.mkdir()
            (run_a / "model.rpt").write_text(_HEALTHY_RPT, encoding="utf-8")
            (run_b / "model.rpt").write_text(_HEALTHY_RPT, encoding="utf-8")
            rc, out, _ = _dispatch(
                [
                    "compare",
                    "--run-a",
                    str(run_a),
                    "--run-b",
                    str(run_b),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("verdict", payload)
        self.assertIn("metric_diffs", payload)

    def test_compare_missing_rpt_returns_nonzero(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_a = base / "ra"
            run_b = base / "rb"
            run_a.mkdir()
            run_b.mkdir()
            # No .rpt anywhere.
            rc, out, _ = _dispatch(
                ["compare", "--run-a", str(run_a), "--run-b", str(run_b)]
            )
        self.assertEqual(rc, 1)
        self.assertIn("incomparable", out)


class CiteCliSmokeTests(unittest.TestCase):
    def test_cite_subcommand_registers(self) -> None:
        parser = build_parser()
        names: set[str] = set()
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:  # type: ignore[attr-defined]
                names.update(action.choices.keys())  # type: ignore[attr-defined]
        self.assertIn("cite", names)

    def test_cite_hit_prints_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_CITATIONS_YAML, encoding="utf-8")
            rc, out, _ = _dispatch(
                [
                    "cite",
                    "worked_example_pending_verification",
                    "--citations-path",
                    str(path),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("worked_example_pending_verification", out)
        self.assertIn("verified: no", out)

    def test_cite_json_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_CITATIONS_YAML, encoding="utf-8")
            rc, out, _ = _dispatch(
                [
                    "cite",
                    "worked_example_pending_verification",
                    "--citations-path",
                    str(path),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["key"], "worked_example_pending_verification")
        self.assertIn("is_verified", payload)

    def test_cite_miss_returns_nonzero(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.yaml"
            path.write_text(_CITATIONS_YAML, encoding="utf-8")
            rc, out, _ = _dispatch(
                ["cite", "no_such_key", "--citations-path", str(path)]
            )
        self.assertEqual(rc, 1)
        self.assertIn("no_such_key", out)


if __name__ == "__main__":
    unittest.main()
