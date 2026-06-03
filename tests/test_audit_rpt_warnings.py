"""Phase E — surface SWMM's own WARNING lines in the audit (issue #4).

A modeller opening a .rpt reads the WARNING lines first (time-step reductions,
minimum slope/elevation enforced, illegal aspect ratios) — they often explain
continuity error or odd hydraulics. The audit screened for ERROR and flooding
but never for native WARNINGs. This adds them as a single **info-level**
diagnostic — informative, never a gate (it must not flip model_diagnostics
status to warning/fail).

Loads ``audit_run.py`` as a module (mirroring the runner-script tests) and
calls the pure functions directly — no subprocess, no real swmm5.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_run", AUDIT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {AUDIT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WARNING_RPT = (
    "  EPA SWMM 5.2 (Build 5.2.4)\n"
    "  WARNING 02: maximum routing time step reduced to 12.34 sec\n"
    "  WARNING 04: minimum elevation drop used for Conduit C1\n"
    "  \n"
    "  Node Flooding Summary\n"
    "  No nodes were flooded.\n"
)

CLEAN_RPT = (
    "  EPA SWMM 5.2\n"
    "  Node Flooding Summary\n"
    "  No nodes were flooded.\n"
)


class ParseRptWarningsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit = load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def _rpt(self, text: str) -> Path:
        p = self.tmp_path / "model.rpt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_warnings_collected_as_single_info_diagnostic(self) -> None:
        diags = self.audit.parse_rpt_warnings(self._rpt(WARNING_RPT))
        self.assertEqual(len(diags), 1)
        d = diags[0]
        self.assertEqual(d["severity"], "info")
        self.assertEqual(d["evidence"]["count"], 2)
        self.assertTrue(any("WARNING 02" in w for w in d["evidence"]["warnings"]))

    def test_clean_rpt_has_no_warnings(self) -> None:
        self.assertEqual(self.audit.parse_rpt_warnings(self._rpt(CLEAN_RPT)), [])

    def test_missing_rpt_returns_empty(self) -> None:
        self.assertEqual(self.audit.parse_rpt_warnings(self.tmp_path / "nope.rpt"), [])

    def test_narrative_warning_word_not_matched(self) -> None:
        # Only the canonical ``WARNING <n>:`` form, not prose or unnumbered.
        rpt = self._rpt("  this is a warning about nothing\n  WARNING: no number\n")
        self.assertEqual(self.audit.parse_rpt_warnings(rpt), [])


class BuildModelDiagnosticsWarningsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit = load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_warnings_included_as_info_without_gating_status(self) -> None:
        rpt = self.tmp_path / "model.rpt"
        rpt.write_text(WARNING_RPT, encoding="utf-8")
        result = self.audit.build_model_diagnostics(
            inp_path=None, rpt_path=rpt, continuity_metric=None, repo_root=self.tmp_path
        )
        ids = [d["id"] for d in result["diagnostics"]]
        self.assertIn("swmm_native_warnings", ids)
        self.assertEqual(result["info_count"], 1)
        # info must NOT flip the diagnostics status away from pass.
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
