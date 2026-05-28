"""CLI surface tests for ``aiswmm map`` (PRD swmmanywhere_integration).

The verb is the spatial-layout counterpart to ``aiswmm plot``. These
tests cover:

* ``aiswmm map --help`` prints the documented flag block.
* Missing ``--run-dir`` exits with the argparse-standard code 2.
* A run directory with a minimal INP renders a PNG to the conventional
  ``07_plots/network_map.png`` location.
* ``--out-png`` overrides the default location.
* ``--no-subcatchments`` and ``--no-vertices`` forward to the renderer.
* The renderer module is independently importable (catches
  regressions where the script's imports drift).

We do not exercise the SWMManywhere geoparquet path here — that
requires the optional ``[anywhere]`` extra which is not part of the
default install. ``test_swmmanywhere_runner.py`` already gates the
extra-dependent behaviour.
"""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser, main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[1]


# A minimal INP carrying just enough sections for the renderer to draw
# something. Two junctions, one outfall, one conduit, one subcatchment
# polygon — covers every layer the script knows about.
_MINIMAL_INP = """[TITLE]
test

[OPTIONS]
FLOW_UNITS           CMS

[SUBCATCHMENTS]
;;Name           Raingage         Outlet         Area    %Imperv  Width   %Slope  CurbLen
S1               RG1              J1             1.0     25       100     1.0     0

[JUNCTIONS]
;;Name           Elevation  MaxDepth  InitDepth  SurDepth  Aponded
J1               100        10        0          0         0

[OUTFALLS]
;;Name           Elevation  Type       Stage Data       Gated   Route To
O1               90         FREE

[CONDUITS]
;;Name           From Node  To Node   Length  Roughness
C1               J1         O1        1000    0.013

[COORDINATES]
;;Node           X-Coord      Y-Coord
J1               100.0        100.0
O1               200.0        200.0

[POLYGONS]
;;Subcatchment   X-Coord      Y-Coord
S1               50.0         50.0
S1               150.0        50.0
S1               150.0        150.0
S1               50.0         150.0
S1               50.0         50.0
"""


def _capture(argv: list[str]) -> tuple[int, str, str]:
    """Run ``cli_main(argv)`` in-process and capture (code, stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _seed_run_dir(parent: Path, *, name: str = "run01") -> Path:
    """Materialise a run directory containing a minimal INP."""
    run_dir = parent / name
    run_dir.mkdir()
    (run_dir / "model.inp").write_text(_MINIMAL_INP, encoding="utf-8")
    return run_dir


class MapCliSurfaceTests(unittest.TestCase):
    """``aiswmm map``'s argparse surface."""

    def test_help_documents_required_and_optional_flags(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "map", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("--run-dir", proc.stdout)
        self.assertIn("--inp", proc.stdout)
        self.assertIn("--out-png", proc.stdout)
        self.assertIn("--no-subcatchments", proc.stdout)
        self.assertIn("--no-vertices", proc.stdout)
        # The --example flag is the uniform copy-pasteable invocation
        # helper every verb exposes.
        self.assertIn("--example", proc.stdout)

    def test_missing_run_dir_exits_2(self) -> None:
        # Subprocess so argparse's SystemExit cleanly returns the
        # expected exit code without polluting the test process.
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "map"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--run-dir", proc.stderr)

    def test_map_appears_in_top_level_help(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "agentic_swmm.cli", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        # 'map' shows up under "Core workflow:" alongside plot.
        self.assertIn("map", proc.stdout)
        # The description must be present so the grouped help never
        # surfaces a bare verb name.
        self.assertIn("Render the spatial layout", proc.stdout)


class MapRendererIntegrationTests(unittest.TestCase):
    """End-to-end CLI -> renderer integration on a minimal INP."""

    def test_default_run_writes_network_map_png(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_dir(Path(tmp))
            code, _, _ = _capture(["map", "--run-dir", str(run_dir)])
            self.assertEqual(code, 0)
            out_png = run_dir / "07_plots" / "network_map.png"
            self.assertTrue(out_png.is_file(), f"missing {out_png}")
            # Sanity: a real PNG starts with the 8-byte PNG magic header.
            with out_png.open("rb") as fh:
                head = fh.read(8)
            self.assertEqual(head, b"\x89PNG\r\n\x1a\n")

    def test_explicit_out_png_overrides_default_location(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_dir(Path(tmp))
            custom = Path(tmp) / "elsewhere" / "custom_map.png"
            code, _, _ = _capture(
                ["map", "--run-dir", str(run_dir), "--out-png", str(custom)]
            )
            self.assertEqual(code, 0)
            self.assertTrue(custom.is_file())
            # Default path must NOT have been created when --out-png is
            # explicit.
            default = run_dir / "07_plots" / "network_map.png"
            self.assertFalse(default.exists())

    def test_no_subcatchments_flag_still_renders(self) -> None:
        # The negative flags are forwarded; we only assert the command
        # still produces a PNG (the contents change but the surface
        # contract holds).
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_dir(Path(tmp))
            code, _, _ = _capture(
                [
                    "map",
                    "--run-dir",
                    str(run_dir),
                    "--no-subcatchments",
                    "--no-vertices",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((run_dir / "07_plots" / "network_map.png").is_file())

    def test_run_dir_without_inp_errors_with_actionable_message(self) -> None:
        # The discovery path must fail fast with a message that names
        # both fallbacks (--inp explicit, or place an INP under the
        # run dir) rather than a confusing FileNotFoundError on a
        # downstream path.
        with TemporaryDirectory() as tmp:
            empty_run = Path(tmp) / "empty"
            empty_run.mkdir()
            code, _, err = _capture(["map", "--run-dir", str(empty_run)])
            self.assertEqual(code, 1)
            self.assertIn("INP", err)

    def test_records_command_trace(self) -> None:
        # ``map`` follows the same trace-append convention as ``plot``.
        # A successful run appends a stage="map" entry to the run dir's
        # command_trace.json.
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_dir(Path(tmp))
            code, _, _ = _capture(["map", "--run-dir", str(run_dir)])
            self.assertEqual(code, 0)
            trace_path = run_dir / "command_trace.json"
            self.assertTrue(trace_path.is_file())
            import json

            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            stages = [entry.get("stage") for entry in payload.get("commands", [])]
            self.assertIn("map", stages)


class MapModuleSmokeTests(unittest.TestCase):
    """Import-time smoke checks for the command + renderer modules."""

    def test_command_module_importable_and_registers(self) -> None:
        # Avoid the python builtin shadowing trap: the module name is
        # ``agentic_swmm.commands.map`` but we always import it via the
        # ``map as map_cmd`` alias so the builtin stays callable.
        from agentic_swmm.commands import map as map_cmd

        # The two-function (register/main) contract every command
        # module exposes.
        self.assertTrue(callable(map_cmd.register))
        self.assertTrue(callable(map_cmd.main))

    def test_renderer_script_is_importable(self) -> None:
        # Pure-import test: confirms the script's top-of-file imports
        # all resolve in the default install (no [anywhere] extra).
        import importlib.util

        script = REPO_ROOT / "skills" / "swmm-plot" / "scripts" / "plot_network_layout.py"
        self.assertTrue(script.is_file())
        spec = importlib.util.spec_from_file_location("plot_network_layout", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The four pure-parsing helpers we rely on must be public.
        self.assertTrue(callable(module.parse_inp_coordinates))
        self.assertTrue(callable(module.parse_inp_polygons))
        self.assertTrue(callable(module.parse_inp_conduits))
        self.assertTrue(callable(module.assign_outfall_colours))

    def test_parser_recognises_map_verb(self) -> None:
        # Belt-and-braces against accidental drops from cli.COMMANDS.
        parser = build_parser()
        # argparse's _SubParsersAction stores known subcommand names
        # in ``choices``. ``map`` must be in there for the verb to be
        # reachable.
        sub_action = next(
            (
                a
                for a in parser._actions
                if a.__class__.__name__ == "_SubParsersAction"
            ),
            None,
        )
        self.assertIsNotNone(sub_action)
        self.assertIn("map", sub_action.choices)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
