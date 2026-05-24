"""Tests for issue #189 — dedupe runner ``manifest.json`` loading.

The #186 implementation read the runner stage's ``manifest.json`` twice
during a single audit pipeline invocation:

1. Inside ``collect_run`` to build provenance.
2. Again at the bottom of ``main()`` via ``find_stage_manifest`` +
   ``read_json`` to feed ``render_run_results_section``.

That's pure waste plus a TOCTOU smell. After #189:

- ``collect_run`` exposes the raw runner manifest dict on the returned
  provenance (under ``provenance["_raw"]["runner_manifest"]``).
- ``render_note`` no longer takes a ``runner_manifest`` parameter — it
  pulls the dict off provenance directly.
- ``main()`` reads the runner ``manifest.json`` exactly once.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def _load_audit_module():
    """Load ``audit_run.py`` as an importable module for unit-testing."""
    spec = importlib.util.spec_from_file_location("_audit_run_dedup", AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_audit_run_dedup"] = module
    spec.loader.exec_module(module)
    return module


def _build_minimal_run_dir(repo_root: Path) -> Path:
    """Build a runnable run-dir layout the audit pipeline accepts."""
    run_dir = repo_root / "runs" / "case-dedup"
    runner = run_dir / "05_runner"
    runner.mkdir(parents=True)
    (runner / "model.rpt").write_text(
        """
        ***** Node Inflow Summary *****
        ------------------------------------------------
          OU2             OUTFALL       0.001       0.061      2    03:15

        ***** Runoff Quantity Continuity *****
        Continuity Error (%) ............. -0.13

        ***** Flow Routing Continuity *****
        Continuity Error (%) ............. -0.004
        """,
        encoding="utf-8",
    )
    (runner / "model.out").write_text("binary-placeholder", encoding="utf-8")
    (runner / "stdout.txt").write_text("", encoding="utf-8")
    (runner / "stderr.txt").write_text("", encoding="utf-8")
    (runner / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "rpt": str(runner / "model.rpt"),
                    "out": str(runner / "model.out"),
                    "stdout": str(runner / "stdout.txt"),
                    "stderr": str(runner / "stderr.txt"),
                },
                "metrics": {
                    "peak": {
                        "node": "OU2",
                        "peak": 0.061,
                        "time_hhmm": "03:15",
                        "source": "Node Inflow Summary",
                    },
                    "continuity": {
                        "runoff_quantity": {
                            "Surface Runoff": {"col1": 0.097, "col2": 44.483},
                            "Continuity Error (%)": -0.13,
                        },
                        "flow_routing": {
                            "Continuity Error (%)": -0.004,
                        },
                    },
                },
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


class CollectRunExposesRawRunnerManifestTests(unittest.TestCase):
    """AC1: ``collect_run`` returns the raw runner manifest dict in provenance."""

    def setUp(self) -> None:
        self.audit_run = _load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = _build_minimal_run_dir(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_provenance_carries_raw_runner_manifest_dict(self) -> None:
        provenance = self.audit_run.collect_run(
            self.run_dir, repo_root=self.repo_root
        )

        raw = provenance.get("_raw") or {}
        runner_manifest = raw.get("runner_manifest")
        self.assertIsInstance(runner_manifest, dict)
        # Sanity: the dict matches what the runner wrote.
        self.assertEqual(runner_manifest.get("return_code"), 0)
        metrics = runner_manifest.get("metrics") or {}
        peak = metrics.get("peak") or {}
        self.assertEqual(peak.get("node"), "OU2")
        self.assertEqual(peak.get("peak"), 0.061)


class RenderNoteSignatureTests(unittest.TestCase):
    """AC2: ``render_note`` no longer accepts a ``runner_manifest`` parameter."""

    def setUp(self) -> None:
        self.audit_run = _load_audit_module()

    def test_render_note_has_three_positional_parameters(self) -> None:
        sig = inspect.signature(self.audit_run.render_note)
        params = list(sig.parameters)
        # provenance, comparison, repo_root — and nothing else.
        self.assertEqual(params, ["provenance", "comparison", "repo_root"])

    def test_render_note_pulls_runner_manifest_off_provenance(self) -> None:
        """render_note must source the runner manifest from provenance._raw."""
        provenance = {
            "run_id": "case-x",
            "status": "pass",
            "generated_at_utc": "1970-01-01T00:00:00Z",
            "_raw": {
                "runner_manifest": {
                    "return_code": 0,
                    "metrics": {
                        "peak": {
                            "node": "OU2",
                            "peak": 0.061,
                            "time_hhmm": "03:15",
                        },
                        "continuity": {
                            "runoff_quantity": {
                                "Surface Runoff": {"col1": 0.097, "col2": 44.483},
                                "Continuity Error (%)": -0.13,
                            },
                            "flow_routing": {
                                "Continuity Error (%)": -0.004,
                            },
                        },
                    },
                }
            },
        }
        comparison = {"comparison_available": False}
        out = self.audit_run.render_note(provenance, comparison, Path("/tmp"))
        self.assertIn("## Run Results", out)
        self.assertIn("`0.061` CMS at node `OU2` at `03:15`", out)


class MainReadsRunnerManifestOnceTests(unittest.TestCase):
    """AC3 + AC6: a typical audit pipeline run reads ``manifest.json`` once."""

    def setUp(self) -> None:
        self.audit_run = _load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = _build_minimal_run_dir(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_runner_manifest_json_is_read_exactly_once(self) -> None:
        """Patch ``read_json`` and count calls against the runner manifest path."""
        runner_manifest_path = (self.run_dir / "05_runner" / "manifest.json").resolve()
        real_read_json = self.audit_run.read_json
        calls: list[Path] = []

        def counting_read_json(path: Path):
            calls.append(Path(path).resolve())
            return real_read_json(path)

        argv = [
            "audit_run.py",
            "--run-dir",
            str(self.run_dir),
            "--repo-root",
            str(self.repo_root),
            "--no-obsidian",
        ]
        with mock.patch.object(self.audit_run, "read_json", side_effect=counting_read_json):
            with mock.patch.object(sys, "argv", argv):
                self.audit_run.main()

        runner_reads = [p for p in calls if p == runner_manifest_path]
        self.assertEqual(
            len(runner_reads),
            1,
            f"expected runner manifest.json read exactly once, got {len(runner_reads)}"
            f" reads. All reads: {calls!r}",
        )


class RunResultsAndKeyMetricsCoexistTests(unittest.TestCase):
    """AC4: both sections survive, with differentiated leading text."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = _build_minimal_run_dir(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_note_keeps_both_sections_with_distinct_intent(self) -> None:
        subprocess.run(
            [
                sys.executable,
                str(AUDIT_SCRIPT),
                "--run-dir",
                str(self.run_dir),
                "--repo-root",
                str(self.repo_root),
                "--no-obsidian",
            ],
            check=True,
            cwd=REPO_ROOT,
        )
        note = (self.run_dir / "09_audit" / "experiment_note.md").read_text(
            encoding="utf-8"
        )
        # Both sections present.
        self.assertIn("## Run Results", note)
        self.assertIn("## Key Metrics", note)
        # Order: Run Results before Key Metrics (Run Results is the headline
        # block, Key Metrics is the source-validation block beneath it).
        self.assertLess(note.index("## Run Results"), note.index("## Key Metrics"))
        # Differentiated leading text — both sections carry a short tagline so
        # the reader sees the intent before the numbers. The exact wording is
        # not asserted; only that *some* leading text follows each header on
        # the very next non-blank line.
        for header in ("## Run Results", "## Key Metrics"):
            idx = note.index(header)
            tail = note[idx + len(header):].lstrip("\n")
            # First non-blank line after the header is a tagline, not a table
            # row (table rows start with ``|``).
            first_line = tail.split("\n", 1)[0]
            self.assertFalse(
                first_line.startswith("|"),
                f"section {header!r} jumps straight to a table; "
                f"expected a short leading tagline first.",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
