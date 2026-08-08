"""Regression tests: CSV time-column inference is priority-ordered and
combines split Date+Time pairs.

Bug class (found 2026-08-08 static sweep, reproduced): both observed-data
readers scanned the FILE's column order for the first name that appeared
in their candidate set, instead of walking candidate priority.

* ``skills/swmm-calibration/scripts/obs_reader.py``: a standard
  hydrometric ``Date,Time,Flow`` export picked the bare Date column, so
  every same-day observation collapsed onto midnight; ``Time,Date,Flow``
  picked bare times that pandas stamped with TODAY's date. Either way
  the corrupted series fed straight into calibration objectives with no
  error.
* ``skills/swmm-uncertainty/scripts/rainfall_ensemble.py``: same defect,
  plus the value column was chosen as "first non-timestamp column",
  which selected the OTHER time column for ``Date,Time,Rainfall``
  headers.

Fix under test: candidate priority (combined names win), split
Date+Time pairs are combined into one timestamp, and the value/flow
column skips every time-role column.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory


_REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_OBS = _load("_obs_reader_ts_test", "skills/swmm-calibration/scripts/obs_reader.py")
_RAIN = _load(
    "_rainfall_ensemble_ts_test",
    "skills/swmm-uncertainty/scripts/rainfall_ensemble.py",
)


def _write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


class ObsReaderSplitDateTimeTests(unittest.TestCase):
    def test_date_time_flow_header_keeps_all_rows_and_real_dates(self) -> None:
        """Pre-fix: 4 rows collapsed to 2 unique midnight timestamps."""
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "obs.csv",
                "Date,Time,Flow\n"
                "1994-01-01,00:00,1.0\n"
                "1994-01-01,00:15,2.0\n"
                "1994-01-01,00:30,3.0\n"
                "1994-01-02,00:00,4.0\n",
            )
            df = _OBS.read_series(path)
        self.assertEqual(len(df), 4)
        self.assertEqual(df["timestamp"].nunique(), 4)
        self.assertEqual(df.iloc[1]["timestamp"], datetime(1994, 1, 1, 0, 15))

    def test_time_date_flow_header_keeps_historical_dates(self) -> None:
        """Pre-fix: bare Time strings were stamped with today's date."""
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "obs.csv",
                "Time,Date,Flow\n"
                "00:00,1994-01-01,1.0\n"
                "00:15,1994-01-01,2.0\n",
            )
            df = _OBS.read_series(path)
        self.assertEqual(len(df), 2)
        years = {ts.year for ts in df["timestamp"]}
        self.assertEqual(years, {1994})

    def test_combined_timestamp_column_still_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "obs.csv",
                "timestamp,flow\n"
                "1994-01-01 00:00,1.0\n"
                "1994-01-01 00:15,2.0\n",
            )
            df = _OBS.read_series(path)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["timestamp"], datetime(1994, 1, 1, 0, 0))

    def test_flow_candidates_walk_priority_not_file_order(self) -> None:
        # "value" appears before "flow" in the file; "flow" has higher
        # candidate priority and must win.
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "obs.csv",
                "timestamp,value,flow\n"
                "1994-01-01 00:00,99.0,1.0\n",
            )
            df = _OBS.read_series(path)
        self.assertEqual(df.iloc[0]["flow"], 1.0)

    def test_explicit_columns_still_override(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "obs.csv",
                "Date,Time,Flow,stage\n"
                "1994-01-01,00:00,1.0,7.7\n",
            )
            df = _OBS.read_series(path, flow_col="stage")
        self.assertEqual(df.iloc[0]["flow"], 7.7)


class RainfallEnsembleSplitDateTimeTests(unittest.TestCase):
    def test_date_time_rainfall_header_parses_and_picks_value(self) -> None:
        """Pre-fix: raised 'cannot parse timestamp' (best case) or read
        the Time column as the rainfall value."""
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "rain.csv",
                "Date,Time,Rainfall_mm\n"
                "1994-01-01,00:00,0.0\n"
                "1994-01-01,00:05,1.2\n",
            )
            series = _RAIN.read_rainfall_series(path)
        self.assertEqual(len(series.timestamps), 2)
        self.assertEqual(series.timestamps[1], datetime(1994, 1, 1, 0, 5))
        self.assertAlmostEqual(float(series.values[1]), 1.2)

    def test_rainfall_before_time_columns_still_picks_rainfall(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "rain.csv",
                "Rainfall_mm,Date,Time\n"
                "0.4,1994-01-01,00:00\n",
            )
            series = _RAIN.read_rainfall_series(path)
        self.assertAlmostEqual(float(series.values[0]), 0.4)
        self.assertEqual(series.timestamps[0], datetime(1994, 1, 1, 0, 0))

    def test_combined_timestamp_header_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp),
                "rain.csv",
                "timestamp,rain\n"
                "1994-01-01 00:00,0.0\n"
                "1994-01-01 00:05,2.5\n",
            )
            series = _RAIN.read_rainfall_series(path)
        self.assertEqual(len(series.timestamps), 2)
        self.assertAlmostEqual(float(series.values[1]), 2.5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
