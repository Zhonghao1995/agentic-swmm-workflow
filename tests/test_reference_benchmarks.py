"""Tests for ``agentic_swmm.memory.reference_benchmarks`` (PRD-06 Phase A.2).

A SWMM modeler wants to know "is NSE 0.65 acceptable for an urban
stormwater model" without reading their notebook. ``reference_benchmarks.yaml``
is a curated, hand-editable table; this module is the typed reader.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.reference_benchmarks import (
    classify_metric,
    load_reference_benchmarks,
    recall_reference_benchmark,
)


_SAMPLE_YAML = """
nse_acceptable_thresholds:
  stormwater_event:
    acceptable: 0.5
    good: 0.65
    excellent: 0.75

continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
"""


class LoaderTests(unittest.TestCase):
    def test_load_returns_dict_for_valid_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference_benchmarks.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            data = load_reference_benchmarks(path)
        self.assertIn("continuity_thresholds_pct", data)
        self.assertEqual(data["continuity_thresholds_pct"]["runoff"]["fail"], 10.0)

    def test_missing_file_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.yaml"
            data = load_reference_benchmarks(path)
        self.assertEqual(data, {})

    def test_malformed_yaml_returns_empty_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(":\n:::not yaml", encoding="utf-8")
            data = load_reference_benchmarks(path)
        self.assertEqual(data, {})


class RecallTests(unittest.TestCase):
    def test_dotted_lookup_returns_nested_value(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference_benchmarks.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            value = recall_reference_benchmark(
                path, "continuity_thresholds_pct.runoff.warn"
            )
        self.assertEqual(value, 5.0)

    def test_missing_key_returns_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference_benchmarks.yaml"
            path.write_text(_SAMPLE_YAML, encoding="utf-8")
            value = recall_reference_benchmark(
                path, "continuity_thresholds_pct.unknown.warn", default=42
            )
        self.assertEqual(value, 42)


class ClassifyMetricTests(unittest.TestCase):
    def test_below_warn_passes(self) -> None:
        thresholds = {"warn": 5.0, "fail": 10.0}
        self.assertEqual(classify_metric(2.5, thresholds), "PASS")

    def test_between_warn_and_fail_warns(self) -> None:
        thresholds = {"warn": 5.0, "fail": 10.0}
        self.assertEqual(classify_metric(7.0, thresholds), "WARN")

    def test_at_or_above_fail_fails(self) -> None:
        thresholds = {"warn": 5.0, "fail": 10.0}
        self.assertEqual(classify_metric(10.0, thresholds), "FAIL")
        self.assertEqual(classify_metric(15.0, thresholds), "FAIL")

    def test_negative_continuity_uses_absolute_value(self) -> None:
        thresholds = {"warn": 5.0, "fail": 10.0}
        # SWMM continuity errors are signed; we care about magnitude.
        self.assertEqual(classify_metric(-7.0, thresholds), "WARN")
        self.assertEqual(classify_metric(-12.0, thresholds), "FAIL")

    def test_empty_thresholds_returns_unknown(self) -> None:
        self.assertEqual(classify_metric(1.0, {}), "UNKNOWN")


class ShippedTemplateTests(unittest.TestCase):
    def test_shipped_template_loads_and_has_continuity_thresholds(self) -> None:
        # The repo ships a template under memory/modeling-memory/.
        repo_root = Path(__file__).resolve().parent.parent
        template = repo_root / "memory" / "modeling-memory" / "reference_benchmarks.yaml"
        self.assertTrue(template.is_file(), f"missing template at {template}")
        data = load_reference_benchmarks(template)
        self.assertIn("continuity_thresholds_pct", data)
        self.assertIn("runoff", data["continuity_thresholds_pct"])


if __name__ == "__main__":
    unittest.main()
