"""Tests for skills/swmm-climate/scripts/format_rainfall.py (issue #235 part 2).

Purpose
-------
``format_rainfall.py`` (~750 lines) had no dedicated test coverage before
this file. It is the deterministic rainfall-ingestion step the rest of the
pipeline (``build_inp``, ``build_raingage_section``) depends on, and
CONTEXT.md's reproducibility invariant ("byte-identical output given the
same input") applies to it: same CSV/.dat + same flags must always produce
the same [TIMESERIES] text and JSON manifest.

This suite locks in the deterministic paths called out in issue #235:

1. Unit conversion — in/hr -> mm/hr (``convert_to_mm_per_hr``) and the
   .dat "volume per day" aliases (``mm_per_day`` / ``in_per_day``), which
   are divided by 24 before the same in->mm conversion is applied.
2. Duplicate-timestamp rejection (``validate_temporal_consistency``).
3. Non-monotonic-timestamp rejection, gated by ``--timestamp-policy``.
4. Multi-station collision handling — both the "good" case (the same
   timestamp reused across two *different* stations is not a collision)
   and the "bad" case (a real duplicate inside one station, among many
   interleaved stations, is still caught and correctly attributed; two
   station ids that sanitize to the same series token are rejected).
5. Window slicing (``filter_records_by_window``), inclusive on both ends.

A small number of end-to-end tests drive ``main()`` directly (sys.argv
patched, no subprocess — the module is plain stdlib and fully importable,
nothing in it is __main__-only) to prove the pieces above wire together
through the real CLI and that identical input + identical flags produce
byte-identical output files.

Entry points used
------------------
Direct function import via ``importlib.util.spec_from_file_location``,
mirroring the loader in ``tests/test_rpt_parser_parity.py``. No repo file
is modified by this suite; all fixtures are synthesized under
``tempfile.TemporaryDirectory()``.

Run with:
    python3.11 -m pytest tests/test_format_rainfall.py -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_RAINFALL_SCRIPT = REPO_ROOT / "skills" / "swmm-climate" / "scripts" / "format_rainfall.py"


# ---------------------------------------------------------------------------
# Module loader (pattern from tests/test_rpt_parser_parity.py::_load_file_module)
# ---------------------------------------------------------------------------


def _load_file_module(name: str, path: Path):
    """Load a Python file as a module by absolute path, isolated from sys.modules.

    The module is registered in sys.modules under ``name`` before execution
    so that dataclass processing (which resolves ``from __future__ import
    annotations`` string annotations via ``sys.modules.get(cls.__module__)``)
    can find it — ``RainRecord`` is a frozen dataclass. Any pre-existing
    entry under ``name`` is saved and restored after loading so test
    isolation is preserved.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    _prev = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if _prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = _prev
    return module


def _load_format_rainfall():
    return _load_file_module("_format_rainfall_under_test", FORMAT_RAINFALL_SCRIPT)


def _rec(mod, station_id: str, ts: str, value: float = 1.0, row: int = 1, source: str = "fixture.csv"):
    """Build a ``RainRecord`` directly, bypassing CSV/.dat parsing.

    Used by the validate_temporal_consistency / filter_records_by_window /
    assign_series_names_by_station tests, which exercise those functions
    in isolation rather than through a file-reading entry point.
    """
    return mod.RainRecord(
        station_id=station_id,
        timestamp=datetime.strptime(ts, "%Y-%m-%d %H:%M"),
        rainfall_mm_per_hr=value,
        source_file=Path(source),
        source_row=row,
    )


def _run_main(mod, argv: list[str]) -> str:
    """Run ``mod.main()`` with a patched ``sys.argv``, capturing stdout.

    ``main()`` reads ``sys.argv`` via argparse and returns nothing; this
    drives it exactly like the real CLI entry point (patched argv, not a
    subprocess) since nothing in the module is __main__-only.
    """
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["format_rainfall.py"] + argv):
        with contextlib.redirect_stdout(buf):
            mod.main()
    return buf.getvalue()


def _timeseries_rows(text: str) -> list[list[str]]:
    """Split each non-header [TIMESERIES] line into whitespace tokens.

    Whitespace-token comparison is robust to the exact column padding in
    ``render_timeseries_lines`` (left-justify width 18 plus a literal
    space), which isn't part of the contract under test.
    """
    return [
        line.split()
        for line in text.splitlines()
        if line.strip() and not line.startswith(";;")
    ]


# ===========================================================================
# 1 — Unit conversion: in/hr -> mm/hr, and .dat mm_per_day / in_per_day / 24
# ===========================================================================


class UnitConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()

    def test_in_per_hr_converts_to_mm_per_hr_exact(self) -> None:
        """1 in/hr must equal exactly 25.4 mm/hr under convert_to_mm_per_hr."""
        result = self.mod.convert_to_mm_per_hr(1.0, units="in_per_hr", policy="convert_to_mm_per_hr")
        self.assertEqual(result, 25.4)

    def test_in_per_hr_conversion_scales_linearly(self) -> None:
        """2.5 in/hr -> 63.5 mm/hr (2.5 * 25.4), not just the 1-inch case."""
        result = self.mod.convert_to_mm_per_hr(2.5, units="in_per_hr", policy="convert_to_mm_per_hr")
        self.assertEqual(result, 63.5)

    def test_mm_per_hr_passthrough_under_convert_policy(self) -> None:
        """mm_per_hr input is untouched even under the convert policy."""
        result = self.mod.convert_to_mm_per_hr(5.0, units="mm_per_hr", policy="convert_to_mm_per_hr")
        self.assertEqual(result, 5.0)

    def test_strict_policy_rejects_inches(self) -> None:
        """--unit-policy strict only accepts mm_per_hr; in_per_hr must raise."""
        with self.assertRaisesRegex(ValueError, "strict.*requires mm_per_hr"):
            self.mod.convert_to_mm_per_hr(1.0, units="in_per_hr", policy="strict")

    def test_read_records_from_file_converts_inches_end_to_end(self) -> None:
        """The CSV-reading entry point applies the same in->mm conversion."""
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "rain_in.csv"
            csv_path.write_text(
                "timestamp,rainfall_in_per_hr\n"
                "2025-06-01 00:00,1.0\n"
                "2025-06-01 00:05,2.0\n",
                encoding="utf-8",
            )
            records = self.mod.read_records_from_file(
                csv_path,
                input_count=1,
                timestamp_column="timestamp",
                value_column="rainfall_in_per_hr",
                station_column=None,
                default_station_id=None,
                input_units="in_per_hr",
                unit_policy="convert_to_mm_per_hr",
                timestamp_format="%Y-%m-%d %H:%M",
            )
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0].rainfall_mm_per_hr, 25.4)
            self.assertEqual(records[1].rainfall_mm_per_hr, 50.8)
            # Single-file, no --station-column -> falls back to "STATION1".
            self.assertEqual(records[0].station_id, "STATION1")

    def test_parse_dat_value_units_day_aliases_carry_24h_divisor(self) -> None:
        """mm_per_day / in_per_day map to (canonical_intensity_unit, 24.0)."""
        self.assertEqual(self.mod.parse_dat_value_units("mm_per_day"), ("mm_per_hr", 24.0))
        self.assertEqual(self.mod.parse_dat_value_units("in_per_day"), ("in_per_hr", 24.0))
        # Plain intensity units carry no divisor.
        self.assertEqual(self.mod.parse_dat_value_units("mm_per_hr"), ("mm_per_hr", None))

    def test_dat_mm_per_day_value_divided_by_24(self) -> None:
        """A 24.0 mm/day .dat row must become exactly 1.0 mm/hr (24 / 24)."""
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = Path(tmp) / "rain.dat"
            dat_path.write_text("RG1 2025 6 1 0 0 24.0\n", encoding="utf-8")
            records, interval_minutes = self.mod.read_records_from_dat(
                dat_path,
                dat_value_units_raw="mm_per_day",
                unit_policy="convert_to_mm_per_hr",
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].rainfall_mm_per_hr, 1.0)
            # interval is forced from the declared 24h divisor (1440 min),
            # independent of how many/few rows the file has.
            self.assertEqual(interval_minutes, 1440)

    def test_dat_in_per_day_divided_by_24_then_converted_to_mm(self) -> None:
        """A 24.0 in/day .dat row -> 1.0 in/hr -> 25.4 mm/hr (both steps compose)."""
        with tempfile.TemporaryDirectory() as tmp:
            dat_path = Path(tmp) / "rain.dat"
            dat_path.write_text("RG1 2025 6 1 0 0 24.0\n", encoding="utf-8")
            records, interval_minutes = self.mod.read_records_from_dat(
                dat_path,
                dat_value_units_raw="in_per_day",
                unit_policy="convert_to_mm_per_hr",
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].rainfall_mm_per_hr, 25.4)
            self.assertEqual(interval_minutes, 1440)


# ===========================================================================
# 2 — Duplicate-timestamp rejection
# ===========================================================================


class DuplicateTimestampRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()

    def test_duplicate_timestamp_same_station_raises_under_strict(self) -> None:
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=1),
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=2),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate timestamp"):
            self.mod.validate_temporal_consistency(records, timestamp_policy="strict")

    def test_duplicate_timestamp_raises_even_under_sort_policy(self) -> None:
        """Actual behavior lock-in: duplicate-timestamp rejection is NOT
        gated by --timestamp-policy. Only the *non-monotonic* (strictly
        decreasing, non-equal) check is policy-gated; an exact duplicate
        timestamp for one station raises unconditionally, even when
        --timestamp-policy sort would otherwise tolerate reordering.
        """
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=1),
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=2),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate timestamp"):
            self.mod.validate_temporal_consistency(records, timestamp_policy="sort")


# ===========================================================================
# 3 — Non-monotonic-timestamp rejection
# ===========================================================================


class NonMonotonicTimestampRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()

    def test_non_monotonic_strict_raises(self) -> None:
        """Decreasing (not equal) timestamps for one station raise under strict."""
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:05", row=1),
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=2),
        ]
        with self.assertRaisesRegex(ValueError, "Non-monotonic timestamp"):
            self.mod.validate_temporal_consistency(records, timestamp_policy="strict")

    def test_non_monotonic_sort_policy_allows_and_flags_unsorted(self) -> None:
        """--timestamp-policy sort tolerates the same input without raising,
        but still reports the station as not input-sorted."""
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:05", row=1),
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=2),
        ]
        result = self.mod.validate_temporal_consistency(records, timestamp_policy="sort")
        self.assertEqual(result, {"RG1": False})

    def test_monotonic_input_marked_sorted_under_strict(self) -> None:
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=1),
            _rec(self.mod, "RG1", "2025-06-01 00:05", row=2),
        ]
        result = self.mod.validate_temporal_consistency(records, timestamp_policy="strict")
        self.assertEqual(result, {"RG1": True})


# ===========================================================================
# 4 — Multi-station collision handling
# ===========================================================================


class MultiStationCollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()

    def test_shared_timestamp_across_different_stations_is_not_a_collision(self) -> None:
        """Duplicate detection is scoped per station_id: two different
        stations sharing a timestamp must not trip the duplicate check."""
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=1),
            _rec(self.mod, "RG2", "2025-06-01 00:00", row=2),
            _rec(self.mod, "RG1", "2025-06-01 00:05", row=3),
            _rec(self.mod, "RG2", "2025-06-01 00:05", row=4),
        ]
        result = self.mod.validate_temporal_consistency(records, timestamp_policy="strict")
        self.assertEqual(result, {"RG1": True, "RG2": True})

    def test_duplicate_within_one_station_among_many_is_still_caught_and_attributed(self) -> None:
        """A real duplicate inside RG2's own series, interleaved with two
        other clean stations, must still raise -- and must name RG2, not
        one of the unaffected stations."""
        records = [
            _rec(self.mod, "RG1", "2025-06-01 00:00", row=1),
            _rec(self.mod, "RG2", "2025-06-01 00:00", row=2),
            _rec(self.mod, "RG3", "2025-06-01 00:00", row=3),
            _rec(self.mod, "RG1", "2025-06-01 00:05", row=4),
            _rec(self.mod, "RG2", "2025-06-01 00:05", row=5),
            _rec(self.mod, "RG2", "2025-06-01 00:05", row=6),  # duplicate for RG2
            _rec(self.mod, "RG3", "2025-06-01 00:05", row=7),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate timestamp") as ctx:
            self.mod.validate_temporal_consistency(records, timestamp_policy="strict")
        self.assertIn("RG2", str(ctx.exception))

    def test_series_name_collision_across_stations_rejected(self) -> None:
        """Two distinct station ids that sanitize to the same series token
        (e.g. 'RG-1' and 'RG.1' both -> 'RG_1') must raise, not silently
        overwrite one series with another."""
        with self.assertRaisesRegex(ValueError, "Derived duplicate series name"):
            self.mod.assign_series_names_by_station(
                station_ids=["RG-1", "RG.1"],
                base_series_name="TS_RAIN",
                series_name_template=None,
            )


# ===========================================================================
# 5 — Window slicing (inclusive on both ends)
# ===========================================================================


class WindowSlicingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()
        self.records = [
            _rec(self.mod, "RG1", ts, row=i)
            for i, ts in enumerate(
                [
                    "2025-06-01 00:00",
                    "2025-06-01 00:05",
                    "2025-06-01 00:10",
                    "2025-06-01 00:15",
                    "2025-06-01 00:20",
                ],
                start=1,
            )
        ]

    def _ts(self, s: str) -> datetime:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")

    def test_window_inclusive_on_both_bounds(self) -> None:
        result = self.mod.filter_records_by_window(
            self.records,
            window_start=self._ts("2025-06-01 00:05"),
            window_end=self._ts("2025-06-01 00:15"),
        )
        self.assertEqual(
            [r.timestamp for r in result],
            [self._ts("2025-06-01 00:05"), self._ts("2025-06-01 00:10"), self._ts("2025-06-01 00:15")],
        )

    def test_window_start_only(self) -> None:
        result = self.mod.filter_records_by_window(
            self.records, window_start=self._ts("2025-06-01 00:10"), window_end=None,
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].timestamp, self._ts("2025-06-01 00:10"))
        self.assertEqual(result[-1].timestamp, self._ts("2025-06-01 00:20"))

    def test_window_end_only(self) -> None:
        result = self.mod.filter_records_by_window(
            self.records, window_start=None, window_end=self._ts("2025-06-01 00:05"),
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].timestamp, self._ts("2025-06-01 00:00"))
        self.assertEqual(result[-1].timestamp, self._ts("2025-06-01 00:05"))

    def test_no_window_returns_all_records_unchanged(self) -> None:
        result = self.mod.filter_records_by_window(self.records, window_start=None, window_end=None)
        self.assertEqual(result, self.records)
        self.assertIsNot(result, self.records)  # filter_records_by_window returns a fresh list

    def test_window_excluding_all_records_returns_empty(self) -> None:
        result = self.mod.filter_records_by_window(
            self.records,
            window_start=self._ts("2025-06-01 01:00"),
            window_end=self._ts("2025-06-01 02:00"),
        )
        self.assertEqual(result, [])


# ===========================================================================
# 6 — End-to-end CLI: ties unit conversion + window slicing + multi-station
#     together through main(), plus a determinism (byte-identical) check.
# ===========================================================================


class EndToEndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_format_rainfall()

    def test_main_multi_station_window_and_unit_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "rain_multi.csv"
            csv_path.write_text(
                "station_id,timestamp,rainfall_in_per_hr\n"
                "RG1,2025-06-01 00:00,0.0\n"
                "RG2,2025-06-01 00:00,0.0\n"
                "RG1,2025-06-01 00:05,0.1\n"
                "RG2,2025-06-01 00:05,0.2\n"
                "RG1,2025-06-01 00:10,0.2\n"
                "RG2,2025-06-01 00:10,0.4\n"
                "RG1,2025-06-01 00:15,0.05\n"
                "RG2,2025-06-01 00:15,0.1\n"
                "RG1,2025-06-01 00:20,0.0\n"
                "RG2,2025-06-01 00:20,0.0\n",
                encoding="utf-8",
            )
            out_json = tmp_path / "rain.json"
            out_ts = tmp_path / "rain_ts.txt"

            _run_main(
                self.mod,
                [
                    "--input", str(csv_path),
                    "--station-column", "station_id",
                    "--value-column", "rainfall_in_per_hr",
                    "--value-units", "in_per_hr",
                    "--unit-policy", "convert_to_mm_per_hr",
                    "--window-start", "2025-06-01 00:05",
                    "--window-end", "2025-06-01 00:15",
                    "--series-name", "TS_RAIN",
                    "--out-json", str(out_json),
                    "--out-timeseries", str(out_ts),
                ],
            )

            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["rows_before_window"], 10)
            self.assertEqual(payload["counts"]["rows"], 6)
            self.assertEqual(payload["counts"]["stations"], 2)
            self.assertEqual(payload["series_names"], ["TS_RAIN_RG1", "TS_RAIN_RG2"])
            self.assertEqual(payload["schema"]["input_value_units"], "in_per_hr")
            self.assertEqual(payload["schema"]["unit_policy"], "convert_to_mm_per_hr")
            # Two stations -> no single global interval is reported.
            self.assertIsNone(payload["range"]["interval_minutes"])
            self.assertEqual(payload["range"]["start"], "2025-06-01T00:05")
            self.assertEqual(payload["range"]["end"], "2025-06-01T00:15")
            for station in payload["stations"]:
                self.assertEqual(station["range"]["interval_minutes"], 5)

            rows = _timeseries_rows(out_ts.read_text(encoding="utf-8"))
            self.assertIn(["TS_RAIN_RG1", "06/01/2025", "00:05", "2.54"], rows)
            self.assertIn(["TS_RAIN_RG1", "06/01/2025", "00:10", "5.08"], rows)
            self.assertIn(["TS_RAIN_RG1", "06/01/2025", "00:15", "1.27"], rows)
            self.assertIn(["TS_RAIN_RG2", "06/01/2025", "00:05", "5.08"], rows)
            self.assertIn(["TS_RAIN_RG2", "06/01/2025", "00:10", "10.16"], rows)
            self.assertIn(["TS_RAIN_RG2", "06/01/2025", "00:15", "2.54"], rows)
            # The excluded 00:00 / 00:20 rows must not appear at all.
            self.assertFalse(any(row[2] in ("00:00", "00:20") for row in rows))

    def test_main_raises_when_window_excludes_all_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "rain.csv"
            csv_path.write_text(
                "timestamp,rainfall_mm_per_hr\n"
                "2025-06-01 00:00,1.0\n"
                "2025-06-01 00:05,2.0\n",
                encoding="utf-8",
            )
            out_json = tmp_path / "rain.json"
            out_ts = tmp_path / "rain_ts.txt"

            with self.assertRaisesRegex(ValueError, "No records remain"):
                _run_main(
                    self.mod,
                    [
                        "--input", str(csv_path),
                        "--window-start", "2025-06-01 01:00",
                        "--window-end", "2025-06-01 02:00",
                        "--out-json", str(out_json),
                        "--out-timeseries", str(out_ts),
                    ],
                )

    def test_identical_rerun_produces_byte_identical_outputs(self) -> None:
        """Same input + same flags must produce byte-identical files on a
        second run -- the reproducibility invariant CONTEXT.md documents."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "rain.csv"
            csv_path.write_text(
                "timestamp,rainfall_mm_per_hr\n"
                "2025-06-01 00:00,0.0\n"
                "2025-06-01 00:05,1.2\n"
                "2025-06-01 00:10,6.5\n",
                encoding="utf-8",
            )
            out_json = tmp_path / "rain.json"
            out_ts = tmp_path / "rain_ts.txt"
            argv = [
                "--input", str(csv_path),
                "--series-name", "TS_DET",
                "--out-json", str(out_json),
                "--out-timeseries", str(out_ts),
            ]

            _run_main(self.mod, argv)
            first_json = out_json.read_text(encoding="utf-8")
            first_ts = out_ts.read_text(encoding="utf-8")

            _run_main(self.mod, argv)
            second_json = out_json.read_text(encoding="utf-8")
            second_ts = out_ts.read_text(encoding="utf-8")

            self.assertEqual(first_json, second_json)
            self.assertEqual(first_ts, second_ts)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
