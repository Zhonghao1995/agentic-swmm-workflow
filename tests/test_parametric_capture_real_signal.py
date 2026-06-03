"""Root-cut fix: parametric capture must record REAL modeling signal.

Diagnosed empirically against a live tecnopolo run: experiment_provenance.json
writes continuity under keys ``runoff_quantity`` / ``flow_routing`` and a
``peak_flow`` block, but ``_record_parametric_from_provenance`` read
``runoff`` / ``flow`` and ignored peak entirely — so every captured row had an
EMPTY ``qa_metrics``. The existing tests hid this by using the wrong keys in
their fixtures too.

These tests use the REAL provenance keys and assert the row is actually
populated with useful signal.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.audit_hook import _record_parametric_from_provenance


def _write_provenance(run_dir: Path, prov: dict) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "experiment_provenance.json").write_text(json.dumps(prov), encoding="utf-8")


def _capture(tmp: Path, prov: dict) -> dict:
    run_dir = tmp / "run"
    _write_provenance(run_dir, prov)
    mem = tmp / "mem"
    mem.mkdir()
    path = _record_parametric_from_provenance(run_dir=run_dir, memory_dir=mem)
    assert path is not None, "capture returned None (skipped) — should have written a row"
    return json.loads(Path(path).read_text(encoding="utf-8").splitlines()[-1])


# Mirrors the live tecnopolo provenance shape exactly.
REAL_PROV = {
    "run_id": "tecnopolo-r1",
    "case_name": "tecnopolo",
    "tools": {"swmm5_version": "5.2.4"},
    "metrics": {
        "continuity_error": {
            "name": "continuity_error",
            "values": {"runoff_quantity": -0.13, "flow_routing": -0.004},
        },
        "peak_flow": {
            "name": "peak_flow",
            "node": "O1",
            "value": 2.47,
            "unit": "CMS",
            "time_hhmm": "01:30",
        },
    },
}


class ParametricRealSignalTests(unittest.TestCase):
    def test_continuity_keys_populate_qa_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            row = _capture(Path(tmp), REAL_PROV)
            self.assertEqual(row["qa_metrics"]["runoff_continuity_pct"], -0.13)
            self.assertEqual(row["qa_metrics"]["flow_continuity_pct"], -0.004)

    def test_peak_flow_captured_when_value_present(self) -> None:
        with TemporaryDirectory() as tmp:
            row = _capture(Path(tmp), REAL_PROV)
            self.assertEqual(row["qa_metrics"]["peak_flow_value"], 2.47)
            self.assertEqual(row["qa_metrics"]["peak_flow_node"], "O1")

    def test_legacy_runoff_flow_keys_still_work(self) -> None:
        prov = json.loads(json.dumps(REAL_PROV))
        prov["metrics"]["continuity_error"]["values"] = {"runoff": -0.2, "flow": 0.05}
        with TemporaryDirectory() as tmp:
            row = _capture(Path(tmp), prov)
            self.assertEqual(row["qa_metrics"]["runoff_continuity_pct"], -0.2)
            self.assertEqual(row["qa_metrics"]["flow_continuity_pct"], 0.05)

    def test_null_peak_value_does_not_break_continuity_capture(self) -> None:
        prov = json.loads(json.dumps(REAL_PROV))
        prov["metrics"]["peak_flow"]["value"] = None
        with TemporaryDirectory() as tmp:
            row = _capture(Path(tmp), prov)
            # continuity still captured, peak fields simply absent
            self.assertEqual(row["qa_metrics"]["runoff_continuity_pct"], -0.13)
            self.assertNotIn("peak_flow_value", row["qa_metrics"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
