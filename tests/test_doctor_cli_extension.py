"""Doctor CLI integration tests for the PRD-08 A.1 extension surface.

These exercise the new ``--json`` / ``--fix`` / ``--yes`` flags via
:func:`agentic_swmm.cli.main` so the wiring (argparse registration +
extension dispatch) is captured.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main


class DoctorJsonFlagTests(unittest.TestCase):
    def test_json_output_contains_required_top_level_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            os.environ["AISWMM_MEMORY_DIR"] = tmp
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    cli_main(["doctor", "--json"])
            finally:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
        # The whole stdout is a single JSON document.
        payload = json.loads(stdout.getvalue())
        for key in (
            "checks",
            "memory_stores",
            "optout_status",
            "llm_provider",
            "grouped_warns",
        ):
            self.assertIn(key, payload)
        # 8 memory stores reported (7 modeling-memory stores +
        # sessions.sqlite row from issue #204).
        self.assertEqual(len(payload["memory_stores"]), 8)
        # 6 opt-out flags reported (PRD-09 adds ANTHROPIC_API_KEY).
        self.assertEqual(len(payload["optout_status"]), 6)
        # PRD-09: the LLM-provider block carries the Claude OAuth flag.
        self.assertIn("claude_oauth_present", payload["llm_provider"])


class DoctorTextOutputTests(unittest.TestCase):
    def test_default_output_contains_three_new_section_headers(self) -> None:
        with TemporaryDirectory() as tmp:
            os.environ["AISWMM_MEMORY_DIR"] = tmp
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    cli_main(["doctor"])
            finally:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
        body = stdout.getvalue()
        # Section headers must all be present per PRD-08 §5.4.
        self.assertIn("Install:", body)
        self.assertIn("Memory stores (", body)
        self.assertIn("Runtime knobs:", body)


class DoctorFixYesTests(unittest.TestCase):
    def test_fix_with_yes_applies_without_prompting(self) -> None:
        recorded: list[list[str]] = []

        class _StubProc:
            returncode = 0

        def _stub_runner(cmd, **_kwargs):
            recorded.append(list(cmd))
            return _StubProc()

        with TemporaryDirectory() as tmp:
            os.environ["AISWMM_MEMORY_DIR"] = tmp
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), mock.patch(
                    "agentic_swmm.commands.doctor.apply_fix_actions",
                    wraps=__import__(
                        "agentic_swmm.commands.doctor_extension",
                        fromlist=["apply_fix_actions"],
                    ).apply_fix_actions,
                ) as fake_apply:
                    cli_main(["doctor", "--fix", "--yes"])
            finally:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
        # apply_fix_actions ran (whether or not there were any actions).
        self.assertTrue(fake_apply.called)

    def test_fix_with_no_actions_prints_no_remediable_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            # Seed every memory store as OK so no bootstrap action is
            # offered. The install checks may produce mcp-drift WARNs
            # depending on the test machine; to be deterministic we
            # patch ``collect_fix_actions`` to return an empty list.
            os.environ["AISWMM_MEMORY_DIR"] = tmp
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), mock.patch(
                    "agentic_swmm.commands.doctor.collect_fix_actions",
                    return_value=[],
                ):
                    cli_main(["doctor", "--fix"])
            finally:
                os.environ.pop("AISWMM_MEMORY_DIR", None)
        self.assertIn("no remediable actions available", stdout.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
