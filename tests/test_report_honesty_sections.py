"""The Word deliverable says what the chat answer says (F-17, F-18).

Live session S01 (2026-09-02): the shell's final answer led with "uncalibrated
first-pass model", "Design review: FAIL (1 pass, 2 fail, 4 warn, 4 needs-data)"
and "8.1 ML flooding loss". The Word report built from the same run printed a
raw Python dict in the continuity cell, carried no review and used the word
"uncalibrated" zero times. The client reads the deliverable.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")
yaml = pytest.importorskip("yaml")

from docx import Document  # noqa: E402

from tests.test_report_generator import (  # noqa: E402
    MINIMAL_PROVENANCE,
    _all_text,
    _heading_texts,
    _make_run_dir,
    _run_script,
)

STRUCTURED_CONTINUITY = {
    "name": "continuity_error",
    "source_artifact": "runner_rpt",
    "source_sections": ["Runoff Quantity Continuity", "Flow Routing Continuity"],
    "unit": "percent",
    "values": {"flow_routing": 0.402, "runoff_quantity": -0.09},
}

DESIGN_REVIEW = {
    "schema_version": "1.0",
    "rulebook_id": "gb50014_template",
    "rulebook_version": "0.1",
    "overall_status": "fail",
    "summary": {"total": 3, "pass": 1, "fail": 1, "warn": 0, "needs_data": 1},
    "disclaimer": "THIS RULEBOOK IS A TEMPLATE FOR ILLUSTRATIVE USE ONLY.\nNumeric thresholds are NOT authoritative.",
    "results": [
        {
            "rule_id": "VELOCITY_MAX",
            "status": "fail",
            "severity": "FAIL",
            "title": "Peak conduit velocity must not exceed maximum allowable",
            "worst_element": {"id": "DGM002393", "value": 5.56, "threshold": "3.0", "result": "fail"},
        },
        {
            "rule_id": "MIN_COVER",
            "status": "needs_data",
            "severity": "WARN",
            "title": "Minimum pipe cover",
            "needs_data_reason": "no ground elevations in the INP",
        },
        {
            "rule_id": "SLOPE_MIN",
            "status": "pass",
            "severity": "WARN",
            "title": "Minimum conduit slope",
            "worst_element": {"id": "DGM000001", "value": 0.004, "threshold": "0.003", "result": "pass"},
        },
    ],
}


def _with_review(run_dir: str) -> None:
    review_dir = Path(run_dir) / "11_review"
    review_dir.mkdir()
    (review_dir / "design_review.json").write_text(json.dumps(DESIGN_REVIEW), encoding="utf-8")


def _generate(tmp_path, run_dir: str) -> Document:
    out = tmp_path / "report.docx"
    result = _run_script(run_dir, str(out))
    assert result.returncode == 0, result.stderr
    return Document(str(out))


class TestContinuityCell:
    def test_a_structured_metric_renders_as_numbers(self, tmp_path):
        provenance = copy.deepcopy(MINIMAL_PROVENANCE)
        provenance["metrics"]["continuity_error"] = STRUCTURED_CONTINUITY
        run_dir = _make_run_dir(tmp_path, provenance=provenance)
        texts = _all_text(_generate(tmp_path, run_dir))
        assert "routing 0.402 / runoff -0.09" in texts
        assert not any("{'name'" in t or "source_artifact" in t for t in texts)

    def test_a_bare_number_still_renders(self, tmp_path):
        provenance = copy.deepcopy(MINIMAL_PROVENANCE)
        provenance["metrics"]["continuity_error"] = 0.5
        run_dir = _make_run_dir(tmp_path, provenance=provenance)
        assert "0.5" in _all_text(_generate(tmp_path, run_dir))


class TestDesignReviewSection:
    def test_the_verdict_and_the_deciding_element_are_in_the_deliverable(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        _with_review(run_dir)
        doc = _generate(tmp_path, run_dir)
        headings = _heading_texts(doc)
        assert any(h.endswith("Design Review") for h in headings)
        texts = _all_text(doc)
        assert any("Overall result: FAIL (1 pass, 1 fail, 0 warn, 1 needs-data)" in t for t in texts)
        assert "Peak conduit velocity must not exceed maximum allowable" in texts
        assert "DGM002393" in texts and "5.56" in texts and "3.0" in texts
        assert "no ground elevations in the INP" in texts
        assert any("TEMPLATE FOR ILLUSTRATIVE USE ONLY" in t for t in texts)

    def test_failures_come_first(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        _with_review(run_dir)
        doc = _generate(tmp_path, run_dir)
        table = next(t for t in doc.tables if t.rows[0].cells[0].text == "Rule")
        statuses = [row.cells[1].text for row in table.rows[1:]]
        assert statuses == ["FAIL", "PASS", "NEEDS_DATA"]

    def test_no_review_is_said_not_hidden(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        texts = _all_text(_generate(tmp_path, run_dir))
        assert "No design review recorded for this run." in texts


class TestEvidenceBoundary:
    def test_an_uncalibrated_model_says_so(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        _with_review(run_dir)
        doc = _generate(tmp_path, run_dir)
        assert any(h.endswith("Evidence Boundary") for h in _heading_texts(doc))
        texts = _all_text(doc)
        assert any("uncalibrated first-pass model" in t for t in texts)
        assert any("returned FAIL (1 fail, 0 warn)" in t for t in texts)
        assert any("does not certify compliance" in t for t in texts)

    def test_without_a_review_the_boundary_still_states_calibration(self, tmp_path):
        run_dir = _make_run_dir(tmp_path)
        texts = _all_text(_generate(tmp_path, run_dir))
        assert any("uncalibrated first-pass model" in t for t in texts)
        assert "No design review was run against this model." in texts

    def test_a_recorded_calibration_status_is_carried(self, tmp_path):
        provenance = copy.deepcopy(MINIMAL_PROVENANCE)
        provenance["calibration"] = {"status": "accepted", "objective": "NSE 0.81"}
        run_dir = _make_run_dir(tmp_path, provenance=provenance)
        texts = _all_text(_generate(tmp_path, run_dir))
        assert any("Calibration status recorded for this run: accepted" in t for t in texts)
        assert not any("uncalibrated first-pass model" in t for t in texts)
