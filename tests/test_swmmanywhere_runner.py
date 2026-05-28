"""Tests for ``agentic_swmm.integrations.swmmanywhere_runner``.

The wrapper hides three concrete gotchas discovered during the SWMManywhere
D1 spike. Tests below pin down their handling so future refactors cannot
silently re-introduce the macOS-arm64-only SIGKILL, the str-vs-Path crash,
or the SWMM-path-with-spaces parsing error.

Tests deliberately avoid invoking real SWMManywhere (heavy geo stack
behind the optional ``[anywhere]`` extra) — only the pure-Python
gotcha-handling helpers and dataclasses are exercised. A separate
``test_smoke.py`` under ``skills/swmm-anywhere/tests/`` is gated by the
extra being installed for end-to-end coverage.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.integrations.swmmanywhere_runner import (
    DEFAULT_OUTFALL_DERIVATION,
    SynthRunError,
    SynthRunResult,
    _coerce_base_dir,
    _install_pyswmm_stub,
    normalize_external_paths,
)


class PyswmmStubTests(unittest.TestCase):
    """Gotcha #1: pyswmm SIGKILL on macOS arm64 — runner installs a stub."""

    def setUp(self) -> None:
        self._saved = sys.modules.pop("pyswmm", None)

    def tearDown(self) -> None:
        if self._saved is None:
            sys.modules.pop("pyswmm", None)
        else:
            sys.modules["pyswmm"] = self._saved

    def test_stub_is_installed_in_sys_modules(self) -> None:
        self.assertNotIn("pyswmm", sys.modules)
        _install_pyswmm_stub()
        self.assertIn("pyswmm", sys.modules)
        self.assertTrue(hasattr(sys.modules["pyswmm"], "Simulation"))

    def test_stub_is_idempotent(self) -> None:
        _install_pyswmm_stub()
        first = sys.modules["pyswmm"]
        _install_pyswmm_stub()
        second = sys.modules["pyswmm"]
        self.assertIs(first, second, "stub install should not replace an already-installed pyswmm")


class CoerceBaseDirTests(unittest.TestCase):
    """Gotcha #2: SWMManywhere ``filepaths.py`` requires Path, not str."""

    def test_string_base_dir_becomes_path(self) -> None:
        config = {"base_dir": "/tmp/some_path", "project": "x"}
        out = _coerce_base_dir(config)
        self.assertIsInstance(out["base_dir"], Path)
        self.assertEqual(out["base_dir"], Path("/tmp/some_path"))

    def test_path_base_dir_passes_through(self) -> None:
        original = Path("/tmp/x")
        config = {"base_dir": original}
        out = _coerce_base_dir(config)
        self.assertIs(out["base_dir"], original)


class NormalizeExternalPathsTests(unittest.TestCase):
    """Gotcha #3: SWMM 5.2 ``ERROR 205`` when RAINGAGES FILE path has spaces."""

    def _make_inp_with_raingages_pointing_to(self, inp_path: Path, file_ref: str) -> None:
        inp_path.write_text(
            "[TITLE]\n;;Project Title/Notes\n\n"
            "[OPTIONS]\nFLOW_UNITS LPS\n\n"
            "[RAINGAGES]\n"
            ";;Name  Format    Interval  SCF  Source\n"
            f"1                INTENSITY 00:05    1        FILE       {file_ref} 1          mm   \n"
            "\n"
            "[SUBCATCHMENTS]\n"
        )

    def test_rewrites_absolute_path_with_spaces_to_relative(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external_dir = tmp_path / "dir with spaces"
            external_dir.mkdir()
            external_file = external_dir / "storm.dat"
            external_file.write_text("dummy storm payload")

            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, str(external_file))

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, (external_file,))
            rewritten = inp.read_text()
            self.assertIn("FILE       storm.dat 1", rewritten)
            self.assertNotIn(str(external_file), rewritten)
            self.assertTrue((inp.parent / "storm.dat").exists())
            self.assertEqual((inp.parent / "storm.dat").read_text(), "dummy storm payload")

    def test_leaves_relative_path_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, "storm.dat")

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, ())
            self.assertIn("FILE       storm.dat 1", inp.read_text())

    def test_leaves_absolute_path_without_spaces_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external_dir = tmp_path / "nospaces"
            external_dir.mkdir()
            external_file = external_dir / "storm.dat"
            external_file.write_text("dummy")

            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, str(external_file))

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, ())
            self.assertIn(str(external_file), inp.read_text())


class DataStructureTests(unittest.TestCase):
    """Frozen dataclasses + exception structure are part of the public API."""

    def test_default_outfall_derivation_has_three_tuned_keys(self) -> None:
        self.assertEqual(
            set(DEFAULT_OUTFALL_DERIVATION),
            {"method", "river_buffer_distance", "outfall_length"},
        )
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["method"], "withtopo")
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["river_buffer_distance"], 300.0)
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["outfall_length"], 200.0)

    def test_synth_run_result_is_frozen(self) -> None:
        result = SynthRunResult(
            inp_path=Path("/x/y/z.inp"),
            run_dir=Path("/x/y"),
            raw_manifest_path=Path("/x/y/00_raw/raw_manifest.json"),
            provenance={},
            stage_durations={},
            warnings=(),
        )
        with self.assertRaises(Exception):
            result.inp_path = Path("/other.inp")  # type: ignore[misc]

    def test_synth_run_error_carries_stage(self) -> None:
        original = ValueError("boom")
        err = SynthRunError("config_build", original)
        self.assertEqual(err.stage, "config_build")
        self.assertIs(err.original_exc, original)
        self.assertIn("config_build", str(err))


if __name__ == "__main__":
    unittest.main()
