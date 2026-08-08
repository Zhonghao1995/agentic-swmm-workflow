"""Regression tests: manifest READERS must resolve the runner manifest.

Same drift class as the design-review fix (#382), swept 2026-08-08. A
canonical run dir holds multiple ``manifest.json`` files: the CLI's
top-level summary (no ``metrics``), ``05_builder/manifest.json``, and
``06_runner/manifest.json`` (the runner schema with ``metrics.peak`` /
``metrics.continuity`` / ``files``). Agent-path runs write NO root
manifest at all (only ``aiswmm run`` does). Three readers resolved the
root only, or in the wrong order:

1. ``digest_render._block_for_run``: the session-end digest read
   ``run_dir/manifest.json``, so peak/continuity silently dropped for
   CLI runs (top schema has no metrics) and the whole block vanished
   for agent runs (no root manifest).
2. ``run_artifacts.read_manifest``: root first, then a sorted ``**``
   glob that surfaces ``05_builder`` before ``06_runner`` — either way
   the ``inp`` / ``files`` fast-path in ``find_inp``/``find_out`` was
   dead and every lookup fell through to convention globs.
3. ``summarize_memory.detect_failure_patterns``: "has a manifest"
   checked the root only, stamping ``missing_manifest`` (escalating to
   ``partial_run``) on every healthy agent-driven run.

Legacy flat runs (root manifest IS the runner manifest) must keep
working in all three.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.digest_render import render_final_summary
from agentic_swmm.agent.swmm_runtime.run_artifacts import (
    find_inp,
    find_out,
    read_manifest,
)


_RUNNER_MANIFEST = {
    "manifest_version": "1.0",
    "inp": "05_builder/model.inp",
    "files": {"rpt": "06_runner/model.rpt", "out": "06_runner/model.out"},
    "metrics": {
        "peak": {"node": "OUT_0", "peak": 0.061, "time_hhmm": "03:15"},
        "continuity": {
            "runoff_quantity": {"Continuity Error (%)": -0.13},
            "flow_routing": {"Continuity Error (%)": -0.004},
        },
    },
    "return_code": 0,
    "run_ok": True,
}

_TOP_MANIFEST = {
    "schema_version": "1.0",
    "generated_by": "aiswmm-run",
    "pipeline": "prepared-inp",
    "outputs": {},
}


def _make_canonical_run(root: Path, *, with_top: bool) -> Path:
    """Agent-style (no top manifest) or CLI-style (top manifest) run."""
    run_dir = root / "canonical"
    (run_dir / "06_runner").mkdir(parents=True)
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "06_runner" / "manifest.json").write_text(
        json.dumps(_RUNNER_MANIFEST), encoding="utf-8"
    )
    (run_dir / "06_runner" / "model.out").write_bytes(b"\x00")
    (run_dir / "05_builder" / "model.inp").write_text(
        "[TITLE]\nx\n", encoding="utf-8"
    )
    (run_dir / "05_builder" / "manifest.json").write_text(
        json.dumps({"stage": "builder", "outputs": {}}), encoding="utf-8"
    )
    if with_top:
        (run_dir / "manifest.json").write_text(
            json.dumps(_TOP_MANIFEST), encoding="utf-8"
        )
    return run_dir


class DigestResolutionTests(unittest.TestCase):
    def test_agent_run_without_root_manifest_renders_metrics(self) -> None:
        """Pre-fix: the digest block vanished entirely for agent runs."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp), with_top=False)
            block = render_final_summary([run_dir])
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", block)
        self.assertIn("Continuity:", block)

    def test_cli_run_with_metricless_top_manifest_renders_metrics(self) -> None:
        """Pre-fix: the top manifest won and peak/continuity dropped."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp), with_top=True)
            block = render_final_summary([run_dir])
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", block)

    def test_legacy_flat_run_still_renders(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "legacy"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text(
                json.dumps(_RUNNER_MANIFEST), encoding="utf-8"
            )
            block = render_final_summary([run_dir])
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", block)


class ReadManifestOrderTests(unittest.TestCase):
    def test_canonical_run_returns_runner_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp), with_top=True)
            manifest = read_manifest(run_dir)
        self.assertIn("metrics", manifest)
        self.assertEqual(manifest["files"]["out"], "06_runner/model.out")

    def test_manifest_fast_path_resolves_inp_and_out(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp), with_top=True)
            manifest = read_manifest(run_dir)
            inp = find_inp(run_dir, manifest)
            out = find_out(run_dir, manifest)
        self.assertIsNotNone(inp)
        self.assertEqual(inp.name, "model.inp")
        self.assertIsNotNone(out)
        self.assertEqual(out.name, "model.out")

    def test_legacy_root_manifest_still_wins_when_no_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "legacy"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text(
                json.dumps(_RUNNER_MANIFEST), encoding="utf-8"
            )
            manifest = read_manifest(run_dir)
        self.assertIn("metrics", manifest)


class SummarizeMemoryManifestPresenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "skills/swmm-modeling-memory/scripts/summarize_memory.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_summarize_memory_manifest_test", script
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.mod = module

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("_summarize_memory_manifest_test", None)

    def _detect(self, run_dir: Path) -> list[str]:
        return self.mod.detect_failure_patterns(
            run_dir=run_dir,
            provenance={
                "status": "pass",
                "qa": {"status": "pass", "fail_count": 0},
                "metrics": {
                    "swmm_return_code": 0,
                    "peak_flow": {"value": 1.0},
                    "continuity_error": -0.1,
                },
                "artifacts": {},
            },
            comparison={},
            model_diagnostics={},
            artifacts_missing=[],
            audit_files_found=[
                "experiment_provenance.json",
                "comparison.json",
                "experiment_note.md",
                "model_diagnostics.json",
            ],
        )

    def test_agent_run_with_stage_manifest_is_not_missing_manifest(self) -> None:
        """Pre-fix: every healthy agent run got missing_manifest+partial_run."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_canonical_run(Path(tmp), with_top=False)
            patterns = self._detect(run_dir)
        self.assertNotIn("missing_manifest", patterns)
        self.assertNotIn("partial_run", patterns)

    def test_run_with_no_manifest_anywhere_still_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "bare"
            (run_dir / "05_builder").mkdir(parents=True)
            (run_dir / "05_builder" / "model.inp").write_text(
                "[TITLE]\nx\n", encoding="utf-8"
            )
            patterns = self._detect(run_dir)
        self.assertIn("missing_manifest", patterns)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
