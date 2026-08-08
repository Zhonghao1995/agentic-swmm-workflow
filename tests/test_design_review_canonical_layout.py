"""Regression tests: ``aiswmm review`` must resolve canonical run layouts.

Bug (found 2026-08-08 live sweep): ``design_review.py`` resolved
``model.rpt`` / ``model.inp`` / ``manifest.json`` at the run-dir ROOT
only, so ``aiswmm review --run-dir <canonical run>`` failed with
"model.rpt not found" on every run the product itself produces
(canonical layout parks the rpt under ``06_runner/`` and the inp under
``05_builder/``). Worse, a canonical run holds TWO manifests: the top
``manifest.json`` (CLI summary schema) and ``06_runner/manifest.json``
(the ``metrics.peak`` / ``metrics.continuity`` schema the reviewer
reads), so even a naive rpt fix that kept root-first manifest order
would silently drop every run-level metric.

Fix under test: ``_find_file`` searches stage dirs FIRST (mirroring the
audit script's stage-name lists), then the root, so canonical runs
resolve the runner manifest and legacy flat runs keep working.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/swmm-design-review/scripts/design_review.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_design_review_layout_test", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


_RPT = (
    "  EPA SWMM 5.2 (Build 5.2.4)\n"
    "  Flow Routing Continuity\n"
)

_RUNNER_MANIFEST = {
    "manifest_version": "1.0",
    "created_at": "2026-08-08T00:00:00+00:00",
    "metrics": {
        "peak": {"node": "O1", "peak": 1.5},
        "continuity": {"continuity_error_percent": {"flow_routing": -0.2}},
    },
    "return_code": 0,
    "run_ok": True,
}

_TOP_MANIFEST = {
    "schema": "aiswmm-run-top-manifest",
    "outputs": {},
}


def _make_canonical_run(root: Path) -> Path:
    run_dir = root / "canonical"
    (run_dir / "06_runner").mkdir(parents=True)
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "06_runner" / "model.rpt").write_text(_RPT, encoding="utf-8")
    (run_dir / "06_runner" / "manifest.json").write_text(
        json.dumps(_RUNNER_MANIFEST), encoding="utf-8"
    )
    (run_dir / "05_builder" / "model.inp").write_text(
        "[TITLE]\ncanonical\n", encoding="utf-8"
    )
    # The decoy: a top-level manifest with the WRONG schema.
    (run_dir / "manifest.json").write_text(
        json.dumps(_TOP_MANIFEST), encoding="utf-8"
    )
    return run_dir


def _make_legacy_run(root: Path) -> Path:
    run_dir = root / "legacy"
    run_dir.mkdir(parents=True)
    (run_dir / "model.rpt").write_text(_RPT, encoding="utf-8")
    (run_dir / "model.inp").write_text("[TITLE]\nlegacy\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(_RUNNER_MANIFEST), encoding="utf-8"
    )
    return run_dir


class FindFileStageResolutionTests(unittest.TestCase):
    def test_canonical_rpt_resolves_from_runner_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp))
            found = _MOD._find_file(
                run_dir, ["model.rpt"], _MOD._RUNNER_STAGE_NAMES
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.parent.name, "06_runner")

    def test_canonical_manifest_prefers_runner_stage_over_top(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp))
            found = _MOD._find_file(
                run_dir,
                ["manifest.json", "runner_manifest.json"],
                _MOD._RUNNER_STAGE_NAMES,
            )
            data = json.loads(found.read_text(encoding="utf-8"))
            self.assertIn("metrics", data)
            self.assertEqual(data["metrics"]["peak"]["peak"], 1.5)

    def test_canonical_inp_resolves_from_builder_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp))
            found = _MOD._find_file(
                run_dir, ["model.inp"], _MOD._BUILDER_STAGE_NAMES
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.parent.name, "05_builder")

    def test_legacy_flat_run_still_resolves_at_root(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_legacy_run(Path(tmp))
            rpt = _MOD._find_file(run_dir, ["model.rpt"], _MOD._RUNNER_STAGE_NAMES)
            inp = _MOD._find_file(run_dir, ["model.inp"], _MOD._BUILDER_STAGE_NAMES)
            man = _MOD._find_file(
                run_dir,
                ["manifest.json", "runner_manifest.json"],
                _MOD._RUNNER_STAGE_NAMES,
            )
            self.assertEqual(rpt.parent, run_dir)
            self.assertEqual(inp.parent, run_dir)
            self.assertEqual(man.parent, run_dir)


class ReviewMainEndToEndTests(unittest.TestCase):
    def _run_main(self, run_dir: Path, out_dir: Path) -> int:
        argv = ["--run-dir", str(run_dir), "--out-dir", str(out_dir)]
        return _MOD.main(argv)

    def test_review_completes_on_canonical_run(self) -> None:
        """Pre-fix: exit 2 with 'model.rpt not found' on every
        canonical-layout run the product itself produces."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp))
            out_dir = Path(tmp) / "review-out"
            code = self._run_main(run_dir, out_dir)
            self.assertNotEqual(code, 2)
            report = out_dir / "design_review.json"
            self.assertTrue(report.exists())
            data = json.loads(report.read_text(encoding="utf-8"))
            # The run-level continuity metric must have come from the
            # RUNNER manifest (the top-manifest decoy has no metrics).
            text = json.dumps(data)
            self.assertIn("continuity", text)

    def test_review_completes_on_legacy_run(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_legacy_run(Path(tmp))
            out_dir = Path(tmp) / "review-out"
            code = self._run_main(run_dir, out_dir)
            self.assertNotEqual(code, 2)
            self.assertTrue((out_dir / "design_review.json").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
