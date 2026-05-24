"""Tests for the always-on ``## Run Results`` section (PRD-183, issue #183).

The audit note must always include a ``## Run Results`` section that
renders the run's key metrics directly from the runner ``manifest.json``,
regardless of whether ``--compare-to`` was provided. Numbers must be
rendered exactly as they appear in the manifest (no extra rounding).

Section ordering is contractual: the new block lives between
``## QA Gates`` and ``## Artifact Index``.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def _load_audit_module():
    """Load ``audit_run.py`` as an importable module for unit-testing helpers."""
    spec = importlib.util.spec_from_file_location("_audit_run_under_test", AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_audit_run_under_test"] = module
    spec.loader.exec_module(module)
    return module


class RenderRunResultsSectionTests(unittest.TestCase):
    """Unit-test the pure renderer against the 4 PRD-mandated fixtures."""

    def setUp(self) -> None:
        self.audit_run = _load_audit_module()

    # Fixture 1: peak + both continuity errors, no internal_node_peak.
    def test_fixture_basic_renders_five_rows_without_internal_node(self) -> None:
        manifest = {
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

        out = self.audit_run.render_run_results_section(manifest)

        self.assertIn("## Run Results", out)
        # The five required rows are present.
        self.assertIn("| Status | PASS |", out)
        self.assertIn("Peak flow at outfall", out)
        self.assertIn("`0.061` CMS at node `OU2` at `03:15`", out)
        self.assertIn("Continuity error", out)
        self.assertIn("runoff quantity", out)
        self.assertIn("`-0.13` %", out)
        self.assertIn("flow routing", out)
        self.assertIn("`-0.004` %", out)
        self.assertIn("Total surface runoff", out)
        self.assertIn("`0.097` hectare-m (`44.483` mm)", out)
        # No internal-node row when manifest omits it.
        self.assertNotIn("Internal node peak", out)

    # Fixture 2: Tecnopolo-style manifest with internal_node_peak populated.
    def test_fixture_tecnopolo_includes_internal_node_row(self) -> None:
        manifest = {
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
                "internal_node_peak": {
                    "node": "J22",
                    "peak": 0.007,
                    "time_hhmm": "03:15",
                },
            },
        }

        out = self.audit_run.render_run_results_section(manifest)

        self.assertIn("Internal node peak", out)
        self.assertIn("`0.007` CMS at node `J22` at `03:15`", out)

    # Fixture 3: manifest missing metrics.peak — only Peak row reads
    # "unavailable", rest of the section still renders.
    def test_fixture_missing_peak_marks_only_that_row_unavailable(self) -> None:
        manifest = {
            "return_code": 0,
            "metrics": {
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

        out = self.audit_run.render_run_results_section(manifest)

        self.assertIn("## Run Results", out)
        # Peak row is present but value is "unavailable".
        peak_lines = [line for line in out.splitlines() if "Peak flow at outfall" in line]
        self.assertEqual(len(peak_lines), 1)
        self.assertIn("unavailable", peak_lines[0])
        # Other rows still carry their values.
        self.assertIn("`-0.13` %", out)
        self.assertIn("`-0.004` %", out)
        self.assertIn("`0.097` hectare-m (`44.483` mm)", out)

    # Fixture 4: manifest absent/unreadable — single "unavailable" line, no table.
    def test_fixture_missing_manifest_renders_single_unavailable_line(self) -> None:
        out = self.audit_run.render_run_results_section(None)

        self.assertIn("## Run Results", out)
        self.assertIn("Run Results unavailable: manifest.json could not be read.", out)
        # No table header sneaks through.
        self.assertNotIn("| Field | Value |", out)


class RunResultsSectionPlacementTests(unittest.TestCase):
    """End-to-end: the new section sits between QA Gates and Artifact Index."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.run_dir = self.repo_root / "runs" / "case-a"
        runner = self.run_dir / "05_runner"
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_results_appears_between_qa_gates_and_artifact_index(self) -> None:
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
        self.assertIn("## QA Gates", note)
        self.assertIn("## Run Results", note)
        self.assertIn("## Artifact Index", note)
        qa_idx = note.index("## QA Gates")
        run_idx = note.index("## Run Results")
        art_idx = note.index("## Artifact Index")
        self.assertLess(qa_idx, run_idx)
        self.assertLess(run_idx, art_idx)
        # Values from manifest.json land verbatim in the section body.
        self.assertIn("`0.061` CMS at node `OU2` at `03:15`", note)
        self.assertIn("`-0.13` %", note)
        self.assertIn("`-0.004` %", note)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
