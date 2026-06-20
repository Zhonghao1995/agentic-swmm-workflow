"""Smoke tests for the swmm-anywhere skill CLI.

Pure-argument-parsing / help tests — no network calls, no SWMManywhere
import (so they pass even without the [anywhere] extra installed).
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "skills" / "swmm-anywhere" / "scripts" / "synth_from_bbox.py"


class CliSmokeTests(unittest.TestCase):
    def test_cli_has_help_output(self) -> None:
        self.assertTrue(CLI.exists(), f"CLI script missing at {CLI}")
        result = subprocess.run(
            [sys.executable, str(CLI), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("bbox", result.stdout.lower())
        self.assertIn("swmmanywhere", result.stdout.lower())

    def test_cli_rejects_missing_bbox(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # argparse returns exit code 2 on missing required arg
        self.assertEqual(result.returncode, 2)
        self.assertIn("bbox", result.stderr.lower())

    def test_cli_help_mentions_config_overrides(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("config-overrides", result.stdout.lower())

    def test_cli_rejects_malformed_config_overrides(self) -> None:
        # The JSON validation fails fast BEFORE the heavy [anywhere] import,
        # so this exercises the override path without the extra installed.
        result = subprocess.run(
            [
                sys.executable, str(CLI),
                "--bbox", "0", "0", "1", "1",
                "--config-overrides", "{not json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("config-overrides", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
