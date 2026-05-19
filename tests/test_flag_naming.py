"""Unit tests for :mod:`agentic_swmm.agent.flag_naming` (PRD-08 A.2).

The module wires the canonical flag names onto an
:class:`argparse.ArgumentParser` and keeps the historical names as
deprecated aliases. The tests assert four contracts:

1. Canonical and alias flags resolve to the same ``args.<dest>``.
2. The alias emits a single ``[deprecated]:`` line on stderr.
3. The canonical flag is silent.
4. ``--example`` prints the verb's example and exits 0.
"""

from __future__ import annotations

import argparse
import io
import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent.flag_naming import (
    BASE_INP_FLAG,
    EXAMPLE_FLAG,
    INP_FLAG,
    JSON_FLAG,
    QUIET_FLAG,
    emit_deprecated_alias_warning,
    register_example_flag,
    register_inp_flag,
    register_json_flag,
    register_library_entry_flag,
    register_path_flag,
    register_quiet_flag,
)


class RegisterInpFlagTests(unittest.TestCase):
    """``--inp`` and ``--base-inp`` resolve to the same destination."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        register_inp_flag(parser)
        return parser

    def test_inp_populates_args_inp(self) -> None:
        args = self._parser().parse_args([INP_FLAG, "model.inp"])
        self.assertEqual(args.inp, Path("model.inp"))

    def test_base_inp_populates_args_inp(self) -> None:
        # We capture stderr because --base-inp emits a deprecation
        # warning; that side effect is tested separately.
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            args = self._parser().parse_args([BASE_INP_FLAG, "model.inp"])
        self.assertEqual(args.inp, Path("model.inp"))

    def test_base_inp_emits_deprecation_warning(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            self._parser().parse_args([BASE_INP_FLAG, "model.inp"])
        self.assertIn("[deprecated]:", buf.getvalue())
        self.assertIn(BASE_INP_FLAG, buf.getvalue())
        self.assertIn(INP_FLAG, buf.getvalue())

    def test_inp_does_not_emit_deprecation_warning(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            self._parser().parse_args([INP_FLAG, "model.inp"])
        self.assertEqual(buf.getvalue(), "")

    def test_required_accepts_alias(self) -> None:
        """``required=True`` is happy when the deprecated alias supplies the value."""
        parser = argparse.ArgumentParser()
        register_inp_flag(parser, required=True)
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            args = parser.parse_args([BASE_INP_FLAG, "model.inp"])
        self.assertEqual(args.inp, Path("model.inp"))

    def test_required_errors_on_missing(self) -> None:
        """No --inp and no --base-inp -> argparse exits 2."""
        parser = argparse.ArgumentParser()
        register_inp_flag(parser, required=True)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit):
                parser.parse_args([])
        self.assertIn("--inp", err.getvalue())


class RegisterPathFlagTests(unittest.TestCase):
    """``--<noun>-path`` produces the canonical path flag.

    Old flag names continue to work as deprecated aliases.
    """

    def _parser_with_calibration_memory(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        register_path_flag(
            parser,
            noun="calibration-memory",
            help_text="path to calibration_memory.jsonl",
            legacy_aliases=("--calibration-store",),
        )
        return parser

    def test_canonical_calibration_memory_path(self) -> None:
        args = self._parser_with_calibration_memory().parse_args(
            ["--calibration-memory-path", "memory/calib.jsonl"]
        )
        self.assertEqual(args.calibration_memory_path, Path("memory/calib.jsonl"))

    def test_legacy_calibration_store_still_works(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            args = self._parser_with_calibration_memory().parse_args(
                ["--calibration-store", "memory/calib.jsonl"]
            )
        self.assertEqual(args.calibration_memory_path, Path("memory/calib.jsonl"))
        self.assertIn("[deprecated]:", buf.getvalue())
        self.assertIn("--calibration-store", buf.getvalue())
        self.assertIn("--calibration-memory-path", buf.getvalue())


class RegisterLibraryEntryFlagTests(unittest.TestCase):
    """``--library-entry`` keeps ``--from-library`` as deprecated alias."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        register_library_entry_flag(
            parser,
            noun="storm-library",
            help_text="storm library key (e.g. 'vancouver_100yr_3hr')",
        )
        return parser

    def test_library_entry_canonical(self) -> None:
        args = self._parser().parse_args(
            ["--storm-library-entry", "vancouver_100yr_3hr"]
        )
        self.assertEqual(args.storm_library_entry, "vancouver_100yr_3hr")

    def test_from_library_alias_warns(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            args = self._parser().parse_args(
                ["--from-library", "vancouver_100yr_3hr"]
            )
        self.assertEqual(args.storm_library_entry, "vancouver_100yr_3hr")
        self.assertIn("[deprecated]:", buf.getvalue())
        self.assertIn("--from-library", buf.getvalue())


class RegisterJsonFlagTests(unittest.TestCase):
    def test_register_json_flag_adds_json(self) -> None:
        parser = argparse.ArgumentParser()
        register_json_flag(parser)
        args = parser.parse_args([JSON_FLAG])
        self.assertTrue(args.json)
        # Default is falsy.
        defaults = parser.parse_args([])
        self.assertFalse(defaults.json)


class RegisterQuietFlagTests(unittest.TestCase):
    def test_register_quiet_flag_adds_quiet(self) -> None:
        parser = argparse.ArgumentParser()
        register_quiet_flag(parser)
        args = parser.parse_args([QUIET_FLAG])
        self.assertTrue(args.quiet)
        defaults = parser.parse_args([])
        self.assertFalse(defaults.quiet)


class RegisterExampleFlagTests(unittest.TestCase):
    """``--example`` prints the text and exits 0."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(exit_on_error=False)
        register_example_flag(
            parser,
            example_text="aiswmm run --inp foo.inp --run-dir runs/x",
        )
        return parser

    def test_example_flag_emits_text_and_exits(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as ctx:
                self._parser().parse_args([EXAMPLE_FLAG])
        # Exit 0 means "user got what they asked for".
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("aiswmm run --inp foo.inp", out.getvalue())

    def test_example_flag_is_non_empty(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit):
                self._parser().parse_args([EXAMPLE_FLAG])
        self.assertTrue(out.getvalue().strip())


class EmitDeprecatedAliasWarningTests(unittest.TestCase):
    """Direct test of the warning helper for callers that bypass argparse."""

    def test_format_and_target(self) -> None:
        target = io.StringIO()
        emit_deprecated_alias_warning(target, old="--old-flag", new="--new-flag")
        text = target.getvalue()
        self.assertIn("[deprecated]:", text)
        self.assertIn("--old-flag", text)
        self.assertIn("--new-flag", text)
        # Output is a single line so log scrapers can grep deterministically.
        self.assertEqual(text.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
