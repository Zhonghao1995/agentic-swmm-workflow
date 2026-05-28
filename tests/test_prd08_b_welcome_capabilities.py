"""PRD-08 Phase B (audit #36, #39): welcome + capabilities polish.

* The first-run welcome now ships memory-verb-based "Things to try"
  examples so the new ``compare`` / ``transfer`` verbs are visible.
* ``aiswmm capabilities`` groups the 30+ tools into categories
  (Build / Run / Audit / Analyze / Memory / ...) with a one-line
  description per tool.
* When the user looks un-onboarded (no OPENAI_API_KEY, empty
  ``memory/modeling-memory/`` dir), the welcome appends a single
  proactive ``aiswmm doctor`` tip.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.welcome import (
    _missing_setup_tip,
    render_extended_welcome,
)
from agentic_swmm.cli import main as cli_main


def _capture(argv: list[str]) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


class WelcomeMemoryVerbsTests(unittest.TestCase):
    def test_things_to_try_includes_at_least_two_memory_verb_examples(self) -> None:
        text = render_extended_welcome()
        # The "Things to try" block must surface at least 2 of the new
        # memory verbs so a returning user discovers them passively.
        memory_verbs = ("compare", "transfer", "cite", "uncertainty")
        hits = [v for v in memory_verbs if v in text]
        self.assertGreaterEqual(
            len(hits),
            2,
            f"warm-intro must mention >=2 memory verbs; saw {hits}",
        )


class MissingSetupTipTests(unittest.TestCase):
    def setUp(self) -> None:
        # Subscription-first: the tip now also suppresses when a Claude
        # subscription is detected. Neutralise the (Keychain-aware) probe
        # so these tests do not depend on the host's login state.
        self._sub_patch = mock.patch(
            "agentic_swmm.agent.provider_preflight.detect_claude_oauth",
            return_value=False,
        )
        self._sub_patch.start()
        self.addCleanup(self._sub_patch.stop)

    def test_tip_fires_when_no_provider_and_memory_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "missing"
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                os.environ.pop("ANTHROPIC_API_KEY", None)
                tip = _missing_setup_tip(memory_dir=empty_dir)
            self.assertIsNotNone(tip)
            self.assertIn("aiswmm login", tip)

    def test_tip_silent_when_api_key_set(self) -> None:
        with TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "missing"
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
                tip = _missing_setup_tip(memory_dir=empty_dir)
            self.assertIsNone(tip)

    def test_tip_silent_when_subscription_detected(self) -> None:
        # A logged-in subscription user is never nagged, even with empty
        # memory and no OpenAI key.
        self._sub_patch.stop()
        with mock.patch(
            "agentic_swmm.agent.provider_preflight.detect_claude_oauth",
            return_value=True,
        ):
            with TemporaryDirectory() as tmp:
                empty_dir = Path(tmp) / "missing"
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OPENAI_API_KEY", None)
                    tip = _missing_setup_tip(memory_dir=empty_dir)
                self.assertIsNone(tip)
        # Re-start so addCleanup's stop() does not error on a stopped patch.
        self._sub_patch.start()

    def test_tip_silent_when_memory_populated(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "modeling-memory"
            memory_dir.mkdir(parents=True)
            (memory_dir / "parametric_memory.jsonl").write_text(
                '{"a":1}\n', encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                os.environ.pop("ANTHROPIC_API_KEY", None)
                tip = _missing_setup_tip(memory_dir=memory_dir)
            self.assertIsNone(tip)


class CapabilitiesGroupingTests(unittest.TestCase):
    def test_output_has_grouped_section_headers(self) -> None:
        stdout, _, code = _capture(["capabilities"])
        self.assertEqual(code, 0)
        # At least two of the expected category headers should appear.
        seen = sum(
            label in stdout
            for label in ("Build:", "Run:", "Audit:", "Analyze:", "Memory:")
        )
        self.assertGreaterEqual(
            seen,
            2,
            f"capabilities should show grouped sections; stdout=\n{stdout}",
        )

    def test_every_tool_has_a_colon_separator(self) -> None:
        # Each line that starts with 4-space-indent and a name should
        # carry a colon separator (``name: description``).
        stdout, _, code = _capture(["capabilities"])
        self.assertEqual(code, 0)
        bad: list[str] = []
        for line in stdout.splitlines():
            if line.startswith("    ") and not line.lstrip().startswith("-"):
                # Skip blank lines under groups and the "X registered" header.
                stripped = line.lstrip()
                if not stripped:
                    continue
                # Group items always carry `name: description`.
                if ":" not in stripped:
                    bad.append(line)
        self.assertFalse(
            bad, f"expected every group item to carry a colon; bad: {bad!r}"
        )

    def test_json_emits_tools_grouped(self) -> None:
        stdout, _, code = _capture(["capabilities", "--json"])
        self.assertEqual(code, 0)
        import json as _json

        payload = _json.loads(stdout)
        self.assertIn("tools_grouped", payload)
        # The grouped mapping must be a dict of lists.
        grouped = payload["tools_grouped"]
        self.assertIsInstance(grouped, dict)
        for items in grouped.values():
            self.assertIsInstance(items, list)


if __name__ == "__main__":
    unittest.main()
