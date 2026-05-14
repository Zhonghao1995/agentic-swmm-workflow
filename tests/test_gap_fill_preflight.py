"""Tests for ``agentic_swmm.gap_fill.preflight`` (PRD-GF-CORE).

The pre-flight scanner is a pure function. Given a tool's declared
input-file argument names and the call's arguments, it returns one
:class:`GapSignal` per missing file. Empty list means "all required
files present on disk".

The scanner does no I/O beyond ``Path.exists()`` and never raises —
malformed input (non-string path, missing key) is treated as
"missing" so the runtime can route a single uniform L1 signal to the
proposer regardless of the failure mode.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.gap_fill.preflight import scan_required_files


class ScanRequiredFilesTests(unittest.TestCase):
    def test_all_files_present_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "rain.csv"
            p.write_text("data", encoding="utf-8")
            signals = scan_required_files(
                tool_name="build_inp",
                required_file_args=("rainfall_file",),
                args={"rainfall_file": str(p)},
            )
            self.assertEqual(signals, [])

    def test_missing_file_emits_signal(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.csv"
            signals = scan_required_files(
                tool_name="build_inp",
                required_file_args=("rainfall_file",),
                args={"rainfall_file": str(missing)},
            )
            self.assertEqual(len(signals), 1)
            sig = signals[0]
            self.assertEqual(sig.severity, "L1")
            self.assertEqual(sig.kind, "file_path")
            self.assertEqual(sig.field, "rainfall_file")
            self.assertEqual(sig.context["tool"], "build_inp")
            self.assertEqual(sig.context["provided_path"], str(missing))

    def test_missing_key_emits_signal(self) -> None:
        signals = scan_required_files(
            tool_name="build_inp",
            required_file_args=("rainfall_file",),
            args={},
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].field, "rainfall_file")
        self.assertIsNone(signals[0].context.get("provided_path"))

    def test_empty_string_path_emits_signal(self) -> None:
        signals = scan_required_files(
            tool_name="build_inp",
            required_file_args=("rainfall_file",),
            args={"rainfall_file": ""},
        )
        self.assertEqual(len(signals), 1)

    def test_non_string_path_emits_signal(self) -> None:
        signals = scan_required_files(
            tool_name="build_inp",
            required_file_args=("rainfall_file",),
            args={"rainfall_file": 42},
        )
        self.assertEqual(len(signals), 1)

    def test_multiple_files_partial_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            present = Path(tmp) / "a.csv"
            present.write_text("data", encoding="utf-8")
            missing = Path(tmp) / "b.csv"
            signals = scan_required_files(
                tool_name="build_inp",
                required_file_args=("a_file", "b_file"),
                args={"a_file": str(present), "b_file": str(missing)},
            )
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0].field, "b_file")

    def test_signals_have_unique_gap_ids(self) -> None:
        signals = scan_required_files(
            tool_name="build_inp",
            required_file_args=("a", "b", "c"),
            args={},
        )
        ids = [s.gap_id for s in signals]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
