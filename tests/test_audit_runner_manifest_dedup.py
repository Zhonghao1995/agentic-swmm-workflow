"""Tests for issue #189 — dedupe runner ``manifest.json`` loading.

The #186 implementation read the runner stage's ``manifest.json`` twice
during a single audit pipeline invocation:

1. Inside ``collect_run`` to build provenance.
2. Again at the bottom of ``main()`` via ``find_stage_manifest`` +
   ``read_json`` to feed ``render_run_results_section``.

That's pure waste plus a TOCTOU smell. After #189 + #196 polish:

- ``collect_run`` returns a ``(provenance, raw_runner_manifest)`` tuple
  so the raw dict is an explicit second return value rather than a
  smuggled key on the persisted provenance dict.
- ``render_note`` takes the raw manifest as an explicit fourth
  parameter.
- ``main()`` reads the runner ``manifest.json`` exactly once.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"

from tests.conftest import load_audit_module, seed_minimal_run_dir  # noqa: E402


class CollectRunExposesRawRunnerManifestTests(unittest.TestCase):
    """AC1: ``collect_run`` returns ``(provenance, raw_runner_manifest)``."""

    def setUp(self) -> None:
        self.audit_run = load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = seed_minimal_run_dir(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collect_run_returns_two_tuple(self) -> None:
        result = self.audit_run.collect_run(self.run_dir, repo_root=self.repo_root)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_first_tuple_element_is_provenance_without_raw_key(self) -> None:
        provenance, _ = self.audit_run.collect_run(
            self.run_dir, repo_root=self.repo_root
        )
        # Provenance must not smuggle ``_raw`` anymore — the raw payload
        # is the explicit second tuple element instead.
        self.assertNotIn("_raw", provenance)

    def test_second_tuple_element_is_raw_runner_manifest_dict(self) -> None:
        _, runner_manifest = self.audit_run.collect_run(
            self.run_dir, repo_root=self.repo_root
        )
        self.assertIsInstance(runner_manifest, dict)
        # Sanity: the dict matches what the runner wrote.
        self.assertEqual(runner_manifest.get("return_code"), 0)
        metrics = runner_manifest.get("metrics") or {}
        peak = metrics.get("peak") or {}
        self.assertEqual(peak.get("node"), "OU2")
        self.assertEqual(peak.get("peak"), 0.061)


class RenderNoteSignatureTests(unittest.TestCase):
    """AC2: ``render_note`` takes the raw manifest as an explicit parameter."""

    def setUp(self) -> None:
        self.audit_run = load_audit_module()

    def test_render_note_signature_accepts_runner_manifest(self) -> None:
        sig = inspect.signature(self.audit_run.render_note)
        params = list(sig.parameters)
        # provenance, comparison, repo_root, runner_manifest.
        self.assertEqual(
            params, ["provenance", "comparison", "repo_root", "runner_manifest"]
        )

    def test_render_note_renders_runner_manifest_passed_explicitly(self) -> None:
        provenance = {
            "run_id": "case-x",
            "status": "pass",
            "generated_at_utc": "1970-01-01T00:00:00Z",
        }
        runner_manifest = {
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
        comparison = {"comparison_available": False}
        out = self.audit_run.render_note(
            provenance, comparison, Path("/tmp"), runner_manifest
        )
        self.assertIn("## Run Results", out)
        self.assertIn("`0.061` CMS at node `OU2` at `03:15`", out)


class PersistedProvenanceHasNoRawKeyTests(unittest.TestCase):
    """AC3: the on-disk ``experiment_provenance.json`` carries no ``_raw`` key."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = seed_minimal_run_dir(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_persisted_provenance_json_omits_raw_key(self) -> None:
        import json

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
        provenance = json.loads(
            (self.run_dir / "09_audit" / "experiment_provenance.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("_raw", provenance)


class MainReadsRunnerManifestOnceTests(unittest.TestCase):
    """AC4: a typical audit pipeline run reads ``manifest.json`` once."""

    def setUp(self) -> None:
        self.audit_run = load_audit_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = seed_minimal_run_dir(self.repo_root)

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
    """AC5: both sections survive, with differentiated leading text."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = seed_minimal_run_dir(self.repo_root)

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
