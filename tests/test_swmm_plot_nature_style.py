"""The swmm-plot skill renders to the Nature figure specification.

Every renderer under ``skills/swmm-plot/scripts/`` goes through the shared
``plot_style`` module and the vendored ``assets/nature.mplstyle`` instead of
hand-setting rcParams. These tests pin the parts of that contract that look
fine on screen and get a figure bounced:

* the stylesheet carries the spec (TrueType text, ticks out, no grid, 7 pt,
  Wong palette, default bbox);
* the vendored stylesheet does not drift from the upstream nature-figures
  skill asset when that skill is present on the machine;
* no renderer hand-sets fonts/ticks, calls ``tight_layout`` or saves with
  ``bbox_inches='tight'`` (the width-changing trap);
* ``plot_style.save_figure`` writes the vector PDF twin beside the PNG and
  refuses figures wider than 183 mm;
* the produced PDFs are 89 mm wide with embedded TrueType text, no Type 3.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "swmm-plot"
SCRIPTS_DIR = SKILL_DIR / "scripts"
STYLE_ASSET = SKILL_DIR / "assets" / "nature.mplstyle"
UPSTREAM_STYLE = Path.home() / ".claude" / "skills" / "nature-figures" / "assets" / "nature.mplstyle"
RENDERERS = (
    SCRIPTS_DIR / "plot_rain_runoff_si.py",
    SCRIPTS_DIR / "plot_network_layout.py",
    SCRIPTS_DIR / "plot_study_area.py",
)
TODCREEK_INP = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"

WONG_HEX = ["000000", "E69F00", "56B4E9", "009E73", "F0E442", "0072B2", "D55E00", "CC79A7"]
MM_PER_PT = 25.4 / 72.0


def _load_plot_style():
    spec = importlib.util.spec_from_file_location("plot_style", SCRIPTS_DIR / "plot_style.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdf_page_size_mm(pdf_bytes: bytes) -> tuple[float, float]:
    match = re.search(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", pdf_bytes
    )
    assert match, "no /MediaBox in PDF"
    x0, y0, x1, y1 = (float(v) for v in match.groups())
    return (x1 - x0) * MM_PER_PT, (y1 - y0) * MM_PER_PT


def _style_lines(path: Path) -> list[str]:
    """Non-comment, non-blank lines with whitespace collapsed."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(re.sub(r"\s+", " ", line))
    return lines


class StyleAssetTests(unittest.TestCase):
    def test_style_asset_pins_the_spec(self) -> None:
        import matplotlib

        rc = matplotlib.rc_params_from_file(str(STYLE_ASSET), use_default_template=False)
        self.assertEqual(rc["pdf.fonttype"], 42, "text must embed as TrueType, never Type 3")
        self.assertEqual(rc["ps.fonttype"], 42)
        self.assertEqual(rc["xtick.direction"], "out")
        self.assertEqual(rc["ytick.direction"], "out")
        self.assertFalse(rc["axes.grid"], "no background gridlines")
        self.assertFalse(rc["axes.spines.top"])
        self.assertFalse(rc["axes.spines.right"])
        self.assertEqual(rc["font.size"], 7)
        self.assertEqual(rc["xtick.labelsize"], 7)
        self.assertEqual(rc["font.family"], ["sans-serif"])
        # matplotlib parses ``savefig.bbox: standard`` to None; only "tight" is wrong.
        self.assertNotEqual(rc["savefig.bbox"], "tight", "never a tight bbox: it changes the width")
        self.assertEqual(rc["savefig.dpi"], 450)
        self.assertTrue(rc["figure.constrained_layout.use"])
        self.assertFalse(rc["legend.frameon"])
        colours = [c["color"].lstrip("#").upper() for c in rc["axes.prop_cycle"]]
        self.assertEqual(colours, WONG_HEX, "colour cycle must be the Wong palette, black first")
        for key in ("axes.linewidth", "lines.linewidth", "xtick.major.width", "patch.linewidth"):
            self.assertGreaterEqual(rc[key], 0.25, key)
            self.assertLessEqual(rc[key], 1.0, key)

    def test_vendored_style_matches_upstream_nature_figures_skill(self) -> None:
        """Drift guard: the vendored stylesheet is a copy of the nature-figures
        skill asset. Only checkable where that skill is installed."""
        if not UPSTREAM_STYLE.is_file():
            self.skipTest(f"upstream style not present at {UPSTREAM_STYLE}")
        self.assertEqual(
            _style_lines(STYLE_ASSET),
            _style_lines(UPSTREAM_STYLE),
            "skills/swmm-plot/assets/nature.mplstyle drifted from the nature-figures "
            "skill asset; copy the upstream settings over (comments may differ).",
        )


class RenderersUseSharedStyleTests(unittest.TestCase):
    FORBIDDEN = (
        ("rcParams.update", "hand-set rcParams; use plot_style.apply_style()"),
        ("tight_layout(", "tight_layout changes the physical size; use layout='constrained'"),
        ("bbox_inches", "a tight bbox silently changes the 89/183 mm width"),
        ("'Arial'", "font family belongs in the stylesheet"),
        ('"Arial"', "font family belongs in the stylesheet"),
        ("direction='in'", "ticks point outward in the spec"),
        ('direction="in"', "ticks point outward in the spec"),
        ("figsize=(", "sizes come from plot_style.figsize()/map_figsize(), never a literal"),
        ("tab10", "categorical colours come from the Wong palette"),
    )

    def test_every_renderer_goes_through_plot_style(self) -> None:
        for script in RENDERERS:
            source = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertIn("from plot_style import", source)
                self.assertIn("apply_style()", source)
                self.assertIn("save_figure(", source)
                self.assertIn('layout="constrained"', source.replace("'", '"'))
                for needle, why in self.FORBIDDEN:
                    self.assertNotIn(needle, source, f"{script.name}: {needle!r}: {why}")

    def test_renderers_import_standalone(self) -> None:
        """Loading a script by path (as the tests and ``aiswmm`` do) must find
        the sibling ``plot_style`` without the scripts dir on sys.path."""
        for script in RENDERERS[:2]:  # study area needs the gis extra
            spec = importlib.util.spec_from_file_location(script.stem, script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.main), script.name)


class PlotStyleHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ps = _load_plot_style()
        self.tmp = Path(tempfile.mkdtemp(prefix="plot-style-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_figsize_is_spec_legal(self) -> None:
        w, h = self.ps.figsize("single")
        self.assertAlmostEqual(w * 25.4, 89.0, places=1)
        self.assertAlmostEqual(h / w, 0.618, places=3)
        w, _ = self.ps.figsize("double")
        self.assertAlmostEqual(w * 25.4, 183.0, places=1)
        w, h = self.ps.figsize(120, height_mm=60)
        self.assertAlmostEqual(w * 25.4, 120.0, places=1)
        self.assertAlmostEqual(h * 25.4, 60.0, places=1)
        with self.assertRaises(self.ps.SpecError):
            self.ps.figsize(200)
        with self.assertRaises(self.ps.SpecError):
            self.ps.figsize("single", height_mm=171)

    def test_map_figsize_follows_the_extent_and_clamps(self) -> None:
        w, h = self.ps.map_figsize((0, 0, 100, 100))
        self.assertAlmostEqual(w * 25.4, 89.0, places=1)
        self.assertAlmostEqual(h * 25.4, 89.0, places=1)
        _, h = self.ps.map_figsize((0, 0, 100, 10))     # very wide -> half-width floor
        self.assertAlmostEqual(h * 25.4, 44.5, places=1)
        _, h = self.ps.map_figsize((0, 0, 10, 100))     # very tall -> 170 mm ceiling
        self.assertAlmostEqual(h * 25.4, 170.0, places=1)

    def test_save_figure_writes_pdf_twin_and_guards_size(self) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        self.ps.apply_style()
        fig, ax = plt.subplots(figsize=self.ps.figsize("single"), layout="constrained")
        ax.plot([0, 1], [0, 1])
        ax.set_xlabel("Time (s)")
        out_png = self.tmp / "nested" / "fig.png"
        written = self.ps.save_figure(fig, out_png, dpi=100)
        plt.close(fig)
        self.assertEqual(set(written), {"pdf", "png"})
        self.assertTrue(out_png.is_file())
        self.assertTrue(out_png.with_suffix(".pdf").is_file(), "vector twin missing")
        w_mm, _ = _pdf_page_size_mm(out_png.with_suffix(".pdf").read_bytes())
        self.assertAlmostEqual(w_mm, 89.0, delta=0.5)

        too_wide, _ = plt.subplots(figsize=(8.0, 3.0), layout="constrained")
        with self.assertRaises(self.ps.SpecError):
            self.ps.save_figure(too_wide, self.tmp / "wide.png")
        plt.close(too_wide)
        self.assertFalse((self.tmp / "wide.png").exists())
        self.assertFalse((self.tmp / "wide.pdf").exists())


class NetworkMapOutputTests(unittest.TestCase):
    """The layout renderer, end to end on the Todcreek example INP."""

    def test_network_map_is_single_column_with_editable_text(self) -> None:
        if not TODCREEK_INP.is_file():
            self.skipTest("Todcreek example INP missing")
        tmp = Path(tempfile.mkdtemp(prefix="plot-map-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out_png = tmp / "network_map.png"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "plot_network_layout.py"),
             "--inp", str(TODCREEK_INP), "--out-png", str(out_png), "--dpi", "100"],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pdf = out_png.with_suffix(".pdf")
        self.assertTrue(out_png.is_file() and pdf.is_file(), "PNG + PDF twin expected")
        pdf_bytes = pdf.read_bytes()
        w_mm, h_mm = _pdf_page_size_mm(pdf_bytes)
        self.assertAlmostEqual(w_mm, 89.0, delta=1.0)
        self.assertLessEqual(h_mm, 170.5)
        self.assertNotIn(b"/Type3", pdf_bytes, "Type 3 fonts mean outlined/uneditable text")
        self.assertTrue(
            b"/CIDFontType2" in pdf_bytes or b"/TrueType" in pdf_bytes,
            "text must be embedded as TrueType (pdf.fonttype 42)",
        )


@pytest.mark.skipif(shutil.which("swmm5") is None, reason="swmm5 binary not available on PATH")
class HydrographOutputTests(unittest.TestCase):
    """The hydrograph renderer, end to end on a Todcreek run built with swmm5."""

    @classmethod
    def setUpClass(cls) -> None:
        if not TODCREEK_INP.is_file():
            raise unittest.SkipTest("Todcreek example INP missing")
        cls.tmp = Path(tempfile.mkdtemp(prefix="plot-hydro-"))
        cls.inp = cls.tmp / "model.inp"
        cls.inp.write_text(TODCREEK_INP.read_text(encoding="utf-8"), encoding="utf-8")
        cls.out = cls.tmp / "model.out"
        proc = subprocess.run(
            ["swmm5", str(cls.inp), str(cls.tmp / "model.rpt"), str(cls.out)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:  # pragma: no cover - fixture must run
            raise RuntimeError(f"swmm5 failed building fixture: {proc.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _render(self, *extra: str) -> Path:
        out_png = self.tmp / f"fig_{len(extra)}.png"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "plot_rain_runoff_si.py"),
             "--inp", str(self.inp), "--out", str(self.out),
             "--rain-ts", "TS_RAIN", "--rain-kind", "intensity_mm_per_hr",
             "--node", "O1", "--out-png", str(out_png), "--dpi", "100", *extra],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return out_png

    def test_single_column_pdf_twin_with_editable_text(self) -> None:
        out_png = self._render()
        pdf = out_png.with_suffix(".pdf")
        self.assertTrue(pdf.is_file(), "vector PDF twin missing beside the PNG")
        pdf_bytes = pdf.read_bytes()
        w_mm, h_mm = _pdf_page_size_mm(pdf_bytes)
        self.assertAlmostEqual(w_mm, 89.0, delta=1.0)
        self.assertLessEqual(h_mm, 170.5)
        self.assertNotIn(b"/Type3", pdf_bytes)
        self.assertTrue(b"/CIDFontType2" in pdf_bytes or b"/TrueType" in pdf_bytes)

    def test_double_column_width(self) -> None:
        out_png = self._render("--width", "double")
        w_mm, _ = _pdf_page_size_mm(out_png.with_suffix(".pdf").read_bytes())
        self.assertAlmostEqual(w_mm, 183.0, delta=1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
