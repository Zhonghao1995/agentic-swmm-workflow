"""Tests for ``agentic_swmm.memory.storm_library`` (PRD-06 §4.4 — Round 2).

Contract:
- :func:`load_storm_library` is tolerant of missing files and malformed
  YAML: ``{}`` in both cases.
- :func:`recall_chicago_spec` returns ``None`` for missing files,
  missing keys, and entries whose values are all ``null`` (the
  schema-only placeholder convention).
- :func:`recall_user_curated` follows the same contract.
- ``aiswmm storm --from-library <key>`` integrates with the loader.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser
from agentic_swmm.memory.storm_library import (
    load_storm_library,
    recall_chicago_spec,
    recall_user_curated,
)


_VALID_LIBRARY_YAML = """\
schema_version: "1.0"

chicago_hyetographs:
  vancouver_100yr_3hr_5min:
    idf_params:
      a: 65.4
      b: 0.08
      c: 0.81
    peak_position: 0.4
    duration_min: 180
    interval_min: 5
    citation: vancouver_idf_pending_verification
  placeholder_entry:
    idf_params:
      a: null
      b: null
      c: null
    peak_position: null
    duration_min: null
    interval_min: null
    citation: null

huff_user_overrides: {}
scs_user_overrides: {}

user_curated:
  example_recorded_event:
    source: "station_id ABC123"
    timeseries_csv: "data/abc123.csv"
    notes: "1-in-100 storm captured 2018-05-13"
  empty_placeholder:
    source: null
    timeseries_csv: null
    notes: null
"""


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


class LoadStormLibraryTests(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self) -> None:
        self.assertEqual(load_storm_library(Path("/nonexistent/library.yaml")), {})

    def test_malformed_yaml_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_text(": :: :::\n  not yaml\n", encoding="utf-8")
            self.assertEqual(load_storm_library(path), {})

    def test_empty_file_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.yaml"
            path.write_text("", encoding="utf-8")
            self.assertEqual(load_storm_library(path), {})

    def test_valid_file_loads_top_level_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "lib.yaml"
            path.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
            data = load_storm_library(path)
            self.assertIn("chicago_hyetographs", data)
            self.assertIn("user_curated", data)
            self.assertEqual(data.get("schema_version"), "1.0")

    def test_non_dict_root_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_text("- one\n- two\n", encoding="utf-8")
            self.assertEqual(load_storm_library(path), {})


class RecallChicagoSpecTests(unittest.TestCase):
    def _make_lib(self, dir_: Path) -> Path:
        path = dir_ / "lib.yaml"
        path.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
        return path

    def test_hit_returns_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            entry = recall_chicago_spec(path, "vancouver_100yr_3hr_5min")
            self.assertIsNotNone(entry)
            assert entry is not None  # narrowing for type
            self.assertEqual(entry["peak_position"], 0.4)
            self.assertEqual(entry["duration_min"], 180)
            self.assertEqual(entry["idf_params"]["a"], 65.4)

    def test_miss_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            self.assertIsNone(recall_chicago_spec(path, "no_such_key"))

    def test_placeholder_entry_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            self.assertIsNone(recall_chicago_spec(path, "placeholder_entry"))

    def test_empty_key_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            self.assertIsNone(recall_chicago_spec(path, ""))
            self.assertIsNone(recall_chicago_spec(path, "   "))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(
            recall_chicago_spec(Path("/nope/lib.yaml"), "vancouver_100yr_3hr_5min")
        )

    def test_empty_chicago_block_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "lib.yaml"
            path.write_text(
                "schema_version: '1.0'\nchicago_hyetographs: {}\n",
                encoding="utf-8",
            )
            self.assertIsNone(recall_chicago_spec(path, "anything"))


class RecallUserCuratedTests(unittest.TestCase):
    def _make_lib(self, dir_: Path) -> Path:
        path = dir_ / "lib.yaml"
        path.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
        return path

    def test_hit_returns_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            entry = recall_user_curated(path, "example_recorded_event")
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry["source"], "station_id ABC123")
            self.assertIn("timeseries_csv", entry)

    def test_miss_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            self.assertIsNone(recall_user_curated(path, "no_such_event"))

    def test_placeholder_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._make_lib(Path(tmp))
            self.assertIsNone(recall_user_curated(path, "empty_placeholder"))


class StormCliFromLibraryTests(unittest.TestCase):
    def test_cli_from_library_writes_dat(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
            out = base / "storm.dat"
            rc, _, _ = _dispatch(
                [
                    "storm",
                    "--from-library",
                    "vancouver_100yr_3hr_5min",
                    "--storm-library",
                    str(lib),
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            text = out.read_text()
            self.assertIn(";;Name", text)

    def test_cli_from_library_missing_entry_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
            rc, _, err = _dispatch(
                [
                    "storm",
                    "--from-library",
                    "no_such_key",
                    "--storm-library",
                    str(lib),
                    "--depth-mm",
                    "25",
                    "--duration-min",
                    "60",
                ]
            )
            self.assertEqual(rc, 1)
            self.assertIn("storm_library", err)

    def test_cli_from_library_placeholder_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "lib.yaml"
            lib.write_text(_VALID_LIBRARY_YAML, encoding="utf-8")
            rc, _, err = _dispatch(
                [
                    "storm",
                    "--from-library",
                    "placeholder_entry",
                    "--storm-library",
                    str(lib),
                    "--depth-mm",
                    "25",
                ]
            )
            self.assertEqual(rc, 1)
            self.assertIn("placeholder", err.lower() + "")


if __name__ == "__main__":
    unittest.main()
