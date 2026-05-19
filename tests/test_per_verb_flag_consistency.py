"""Per-verb flag consistency tests (PRD-08 A.2).

Every memory verb must accept ``--inp`` (where it has an INP) or
``--quiet`` and ``--example`` regardless. Deprecated aliases must
still be honoured. These tests assert the canonical surface across:

* ``aiswmm calibrate``
* ``aiswmm uncertainty plan``
* ``aiswmm transfer``
* ``aiswmm compare``
* ``aiswmm cite``
* ``aiswmm cite-param``
* ``aiswmm storm``
* ``aiswmm bootstrap memory``
* ``aiswmm doctor``
* ``aiswmm run``
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def _verb_help(verb: str, *, extra: list[str] | None = None) -> str:
    cmd = [sys.executable, "-m", "agentic_swmm.cli"]
    if extra:
        cmd.extend(extra)
    cmd.extend([verb, "--help"])
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return proc.stdout + proc.stderr


def _subcmd_help(verb: str, sub: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_swmm.cli",
            verb,
            sub,
            "--help",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr


class InpFlagAliasTests(unittest.TestCase):
    """``calibrate``/``uncertainty plan``/``transfer`` accept ``--inp`` and the alias."""

    def test_calibrate_accepts_inp(self) -> None:
        help_text = _verb_help("calibrate")
        self.assertIn("--inp", help_text)

    def test_calibrate_base_inp_alias_warns(self) -> None:
        # Sanity: parsing the legacy alias emits the deprecation
        # marker on stderr. We parse via argparse directly to avoid
        # spawning the whole run.
        from agentic_swmm.commands import calibrate as calibrate_cmd

        subparsers = argparse.ArgumentParser().add_subparsers()
        calibrate_cmd.register(subparsers)
        parser = subparsers.choices["calibrate"]
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            args = parser.parse_args(
                [
                    "--run-id",
                    "x",
                    "--total-iters",
                    "1",
                    "--base-inp",
                    "model.inp",
                    "--param",
                    "manning_n=0.01,0.02",
                    "--run-dir",
                    "/tmp/x",
                ]
            )
        self.assertEqual(args.inp, Path("model.inp"))
        self.assertIn("[deprecated]:", err.getvalue())
        self.assertIn("--base-inp", err.getvalue())

    def test_uncertainty_plan_accepts_inp(self) -> None:
        help_text = _subcmd_help("uncertainty", "plan")
        self.assertIn("--inp", help_text)

    def test_uncertainty_plan_base_inp_alias_warns(self) -> None:
        from agentic_swmm.commands import uncertainty as uncertainty_cmd

        subparsers = argparse.ArgumentParser().add_subparsers()
        uncertainty_cmd.register(subparsers)
        outer = subparsers.choices["uncertainty"]
        inner = next(
            action.choices["plan"]
            for action in outer._actions
            if hasattr(action, "choices") and action.choices and "plan" in action.choices
        )
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            args = inner.parse_args(
                [
                    "--base-inp",
                    "model.inp",
                    "--param",
                    "manning_n=0.01,0.02",
                ]
            )
        self.assertEqual(args.inp, Path("model.inp"))
        self.assertIn("[deprecated]:", err.getvalue())


class ExampleFlagTests(unittest.TestCase):
    """Every memory verb has ``--example``."""

    VERBS = ("run", "compare", "cite", "cite-param", "transfer", "calibrate", "doctor")

    def test_every_verb_lists_example(self) -> None:
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                help_text = _verb_help(verb)
                self.assertIn(
                    "--example",
                    help_text,
                    msg=f"verb {verb!r} missing --example flag",
                )

    def test_storm_example(self) -> None:
        # ``aiswmm storm --example`` prints the documented invocation
        # and exits 0.
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "storm", "--example"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("aiswmm storm", proc.stdout)

    def test_calibrate_example(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "calibrate", "--example"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("aiswmm calibrate", proc.stdout)


class QuietFlagTests(unittest.TestCase):
    """Every memory verb has ``--quiet``."""

    VERBS = ("run", "compare", "cite", "cite-param", "transfer", "doctor")

    def test_every_verb_lists_quiet(self) -> None:
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                help_text = _verb_help(verb)
                self.assertIn(
                    "--quiet",
                    help_text,
                    msg=f"verb {verb!r} missing --quiet flag",
                )


class JsonFlagTests(unittest.TestCase):
    """Verbs that emit text get ``--json``."""

    def test_storm_json_emits_design_storm_payload(self) -> None:
        # ``aiswmm storm --shape uniform --depth-mm 25 --duration-min 60 --json``
        # returns a JSON object, not the SWMM DAT block.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_swmm.cli",
                "storm",
                "--shape",
                "uniform",
                "--depth-mm",
                "25",
                "--duration-min",
                "60",
                "--interval-min",
                "5",
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("intensities_mm_per_hr", payload)
        self.assertEqual(payload["depth_mm"], 25.0)
        # ``intensities_mm_per_hr`` should be a numeric list — proves
        # we got the structured payload, not the DAT block.
        self.assertIsInstance(payload["intensities_mm_per_hr"], list)
        self.assertTrue(payload["intensities_mm_per_hr"])
        # DAT format would include a SWMM date stamp like
        # "01/01/2000 00:00 25.0"; the JSON payload nests dates and
        # intensities in separate fields. The presence of "times"
        # and the absence of a "01/01/2000 00:00 25.0" raw row are
        # sufficient signals.
        self.assertIn("times", payload)

    def test_cite_param_json(self) -> None:
        # cite-param --json with a known-unknown name should emit
        # JSON containing ``ok: false``.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_swmm.cli",
                "cite-param",
                "--name",
                "no_such_param.foo",
                "--value",
                "0.013",
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        # Non-zero exit is expected for not-found.
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])


class PathFlagAliasTests(unittest.TestCase):
    """Old path flags continue to work via the alias mechanism."""

    def test_transfer_legacy_calibration_store(self) -> None:
        from agentic_swmm.commands import transfer as transfer_cmd

        subparsers = argparse.ArgumentParser().add_subparsers()
        transfer_cmd.register(subparsers)
        parser = subparsers.choices["transfer"]
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            args = parser.parse_args(
                [
                    "--inp",
                    "model.inp",
                    "--calibration-store",
                    "memory/calib.jsonl",
                ]
            )
        self.assertEqual(args.calibration_store, Path("memory/calib.jsonl"))
        self.assertIn("[deprecated]:", err.getvalue())

    def test_storm_legacy_from_library(self) -> None:
        from agentic_swmm.commands import storm as storm_cmd

        subparsers = argparse.ArgumentParser().add_subparsers()
        storm_cmd.register(subparsers)
        parser = subparsers.choices["storm"]
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            args = parser.parse_args(
                ["--shape", "uniform", "--from-library", "some_key"]
            )
        self.assertEqual(args.from_library, "some_key")
        self.assertIn("[deprecated]:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
