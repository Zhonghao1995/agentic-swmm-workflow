"""Tests for the ``aiswmm`` case subcommands (PRD-CASE-ID).

Round-trips the three new CLI surfaces:
    aiswmm list cases
    aiswmm case init <id>
    aiswmm case show <id>

All three are deterministic and read-only on the repo working tree,
so they are safe to drive from a TemporaryDirectory and verify with
stdout capture.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main


class ListCasesCommandTests(unittest.TestCase):
    def test_empty_repo_prints_no_cases_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with mock.patch("agentic_swmm.case.case_registry.repo_root", return_value=Path(tmp)):
                with redirect_stdout(buf):
                    rc = cli_main(["list", "cases"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # Either an explicit "no cases" line or an empty list marker —
        # we just need stdout to be non-crashing and contain a hint.
        self.assertTrue("no cases" in out.lower() or out.strip() == "[]" or "0 cases" in out.lower() or out.strip() == "")


class CaseInitShowRoundTripTests(unittest.TestCase):
    def test_init_then_show_round_trip(self) -> None:
        env_overrides = {"AISWMM_HEADLESS": "1"}
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with mock.patch.dict(os.environ, env_overrides, clear=False):
                with mock.patch("agentic_swmm.case.case_registry.repo_root", return_value=repo):
                    # init: in headless mode, accept --display-name as the minimum.
                    rc_init = cli_main(
                        [
                            "case",
                            "init",
                            "demo",
                            "--display-name",
                            "Demo Case",
                            "--study-purpose",
                            "throwaway",
                        ]
                    )
                    self.assertEqual(rc_init, 0)
                    self.assertTrue((repo / "cases" / "demo" / "case_meta.yaml").exists())

                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc_show = cli_main(["case", "show", "demo"])
                    self.assertEqual(rc_show, 0)
                    out = buf.getvalue()
                    self.assertIn("demo", out)
                    self.assertIn("Demo Case", out)

    def test_init_rejects_bad_slug(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with mock.patch.dict(os.environ, {"AISWMM_HEADLESS": "1"}, clear=False):
                with mock.patch("agentic_swmm.case.case_registry.repo_root", return_value=repo):
                    rc = cli_main(
                        [
                            "case",
                            "init",
                            "BAD ID",
                            "--display-name",
                            "Bad",
                        ]
                    )
            self.assertNotEqual(rc, 0)

    def test_show_missing_case_fails_cleanly(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with mock.patch("agentic_swmm.case.case_registry.repo_root", return_value=repo):
                rc = cli_main(["case", "show", "absent"])
            self.assertNotEqual(rc, 0)


class CaseIdFlagPresentTests(unittest.TestCase):
    """The ``--case-id`` flag must be visible on the relevant subcommands.

    We do not exercise the full agent pipeline here (too heavy); we
    just parse the args namespace and check the field is present.
    """

    def test_run_subcommand_accepts_case_id(self) -> None:
        from agentic_swmm.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["run", "--inp", "model.inp", "--run-dir", "/tmp/x", "--case-id", "tod-creek"]
        )
        self.assertEqual(getattr(args, "case_id", None), "tod-creek")

    def test_agent_subcommand_accepts_case_id(self) -> None:
        from agentic_swmm.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "--case-id", "tod-creek", "hello"])
        self.assertEqual(getattr(args, "case_id", None), "tod-creek")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
