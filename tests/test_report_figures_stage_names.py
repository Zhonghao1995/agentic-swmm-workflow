"""Regression tests: report figures embed from every real plot-dir name.

Bug (found 2026-08-09 NL sweep): the report's Figures section scanned
``07_plot`` and ``08_plot``, but real runs carry figures under the
canonical ``08_plot`` OR the legacy ``07_plots`` (generation-B CLI
runs, and the name a planner freehand-writes most often). A run whose
figures sat in ``07_plots/`` produced a Word report with "No figures
available", which read as "the product cannot embed figures".

Fix under test: both the code default and the shipped template scan
``08_plot``, ``07_plots``, ``07_plot`` in that order.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document


_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = str(_REPO / "skills/swmm-report/scripts/generate_report.py")


def _png_bytes() -> bytes:
    """A valid 1x1 red PNG, python-docx embeddable."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


def _make_audited_run(tmp: Path, plot_dir_name: str) -> str:
    run_dir = tmp / "run"
    (run_dir / "09_audit").mkdir(parents=True)
    (run_dir / plot_dir_name).mkdir()
    (run_dir / plot_dir_name / "hydrograph.png").write_bytes(_png_bytes())
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "09_audit" / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "run_id": "fig-test",
                "status": "pass",
                "qa": {"status": "pass", "fail_count": 0, "checks": []},
                "metrics": {"swmm_return_code": 0},
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "model_diagnostics.json").write_text(
        json.dumps({"status": "pass", "diagnostics": [],
                    "error_count": 0, "warning_count": 0}),
        encoding="utf-8",
    )
    (run_dir / "09_audit" / "comparison.json").write_text(
        json.dumps({"comparison_available": False, "checks": [], "warnings": []}),
        encoding="utf-8",
    )
    return str(run_dir)


def _image_count(docx_path: str) -> int:
    doc = Document(docx_path)
    return len(doc.inline_shapes)


class ReportFiguresStageNameTests(unittest.TestCase):
    def _generate(self, run_dir: str, out: str) -> None:
        result = subprocess.run(
            [sys.executable, _SCRIPT, "--run-dir", run_dir, "--out", out],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_08_plot_figures_embed(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_audited_run(Path(tmp), "08_plot")
            out = str(Path(tmp) / "r.docx")
            self._generate(run_dir, out)
            self.assertGreaterEqual(_image_count(out), 1)

    def test_legacy_07_plots_figures_embed(self) -> None:
        """The live-observed miss: figures in 07_plots were skipped."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_audited_run(Path(tmp), "07_plots")
            out = str(Path(tmp) / "r.docx")
            self._generate(run_dir, out)
            self.assertGreaterEqual(_image_count(out), 1)

    def test_raw_stage_study_area_map_embeds(self) -> None:
        """The study-area map lands in 00_raw beside the unpacked
        SWMMCanada bundle (user decision 2026-08-09) and must flow
        into the Word deliverable's figures section."""
        with TemporaryDirectory() as tmp:
            run_dir = _make_audited_run(Path(tmp), "00_raw")
            out = str(Path(tmp) / "r.docx")
            self._generate(run_dir, out)
            self.assertGreaterEqual(_image_count(out), 1)

    def test_original_07_plot_still_embeds(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _make_audited_run(Path(tmp), "07_plot")
            out = str(Path(tmp) / "r.docx")
            self._generate(run_dir, out)
            self.assertGreaterEqual(_image_count(out), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
