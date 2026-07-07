"""Pytest coverage for skills/swmm-builder/scripts/build_swmm_inp.py (issue #235).

WHY THIS FILE EXISTS
--------------------
build_swmm_inp.py (~1300 lines) is the last hop before a model is runnable:
subcatchments CSV + params JSON + network JSON + climate references go in,
a runnable INP + a manifest artifact come out.  Before this file, it was
exercised only by scripts/acceptance/run_acceptance.py -- an end-to-end
pipeline script invoked as a subprocess, not collected by pytest -- so a
regression in section emission or input validation would not fail CI.

This file also guards CONTEXT.md invariant #6 ("Reproducibility is
byte-level for SWMM execution ... do not introduce nondeterminism"): the
INP text this script emits is the first artifact in that reproducibility
chain, so build_swmm_inp.py must be a pure function of its inputs -- the
same subcatchments/params/network/climate inputs must always produce
byte-identical INP text.  See
HappyPathBuildTest.test_building_twice_from_same_inputs_is_byte_identical.

COVERAGE
--------
1. Happy path -- builds a full INP from the swmm-builder skill's shipped
   example fixtures and asserts the emitted [SUBCATCHMENTS], [JUNCTIONS],
   and [TIMESERIES] sections are populated with the expected rows, and
   that the manifest artifact reports matching counts, a clean validation
   dict, and a sha256 that matches the actual written INP bytes.
2. Determinism -- building twice from the same inputs is byte-identical.
3. Negative -- pct_imperv outside [0, 100] is rejected (both directions).
4. Negative -- a subcatchment outlet referencing a node absent from
   [JUNCTIONS]/[OUTFALLS] is rejected, and no INP is written.

ENTRY POINTS UNDER TEST
------------------------
Loaded via importlib.util.spec_from_file_location (the pattern
tests/test_rpt_parser_parity.py uses, for the same reason: the script is
deliberately agentic_swmm-import-free and runnable standalone).
  - main()                          -- full CLI pipeline, sys.argv patched
  - validate_and_normalize_params() -- per-field parameter validation

FIXTURE CHAIN
-------------
build_swmm_inp.py's own params-JSON input is itself produced by other
swmm-params / swmm-climate skill scripts.  _build_params_and_rainfall()
below mirrors scripts/acceptance/run_acceptance.py (stages 02-06) and the
private fixture helper in tests/test_wq_builder_smoke.py, skipping only the
swmm-gis preprocessing stage because
skills/swmm-builder/examples/subcatchments_input.csv already ships in
builder-ready CSV form.  Those pre-processing scripts are not the module
under test, so they are run via subprocess rather than importlib.

Run with:
    python3.11 -m pytest tests/test_build_swmm_inp.py -v
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

BUILDER_SCRIPT = REPO_ROOT / "skills" / "swmm-builder" / "scripts" / "build_swmm_inp.py"

# Shipped example fixtures used verbatim for the happy path -- the same
# files scripts/acceptance/run_acceptance.py and tests/test_wq_builder_smoke.py
# build from (minus the swmm-gis preprocessing stage: subcatchments_input.csv
# already ships in builder-ready CSV form, so no GIS step is needed here).
SUBCATCHMENTS_CSV = REPO_ROOT / "skills" / "swmm-builder" / "examples" / "subcatchments_input.csv"
NETWORK_JSON = REPO_ROOT / "skills" / "swmm-network" / "examples" / "basic-network.json"
CONFIG_JSON = REPO_ROOT / "skills" / "swmm-builder" / "examples" / "options_config.json"
LANDUSE_CSV = REPO_ROOT / "skills" / "swmm-params" / "examples" / "landuse_input.csv"
SOIL_CSV = REPO_ROOT / "skills" / "swmm-params" / "examples" / "soil_input.csv"
RAINFALL_CSV = REPO_ROOT / "skills" / "swmm-climate" / "examples" / "rainfall_event.csv"

LANDUSE_SCRIPT = REPO_ROOT / "skills" / "swmm-params" / "scripts" / "landuse_to_swmm_params.py"
SOIL_SCRIPT = REPO_ROOT / "skills" / "swmm-params" / "scripts" / "soil_to_greenampt.py"
MERGE_SCRIPT = REPO_ROOT / "skills" / "swmm-params" / "scripts" / "merge_swmm_params.py"
CLIMATE_SCRIPT = REPO_ROOT / "skills" / "swmm-climate" / "scripts" / "format_rainfall.py"

# Expected shape of the shipped fixture set, asserted rather than assumed --
# skills/swmm-builder/examples/subcatchments_input.csv routes S1/S2 -> J1,
# S3 -> J2, S4 -> OF1; skills/swmm-network/examples/basic-network.json
# defines exactly J1, J2, OF1.
_EXPECTED_SUBCATCHMENT_OUTLETS = {"S1": "J1", "S2": "J1", "S3": "J2", "S4": "OF1"}
_EXPECTED_JUNCTION_ELEVATIONS = {"J1": "100", "J2": "99.2"}
_EXPECTED_TIMESERIES_ROWS = 9  # data rows in skills/swmm-climate/examples/rainfall_event.csv


# ---------------------------------------------------------------------------
# Module loader -- same pattern as tests/test_rpt_parser_parity.py's
# _load_file_module: build_swmm_inp.py is deliberately agentic_swmm-import
# free and runnable standalone, so it is loaded by path rather than through
# a package import.
# ---------------------------------------------------------------------------


def _load_builder_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("build_swmm_inp_under_test", BUILDER_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"Cannot load {BUILDER_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("build_swmm_inp_under_test")
    sys.modules["build_swmm_inp_under_test"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("build_swmm_inp_under_test", None)
        else:
            sys.modules["build_swmm_inp_under_test"] = previous
    return module


def _run_builder(
    module: types.ModuleType,
    *,
    subcatchments_csv: Path,
    params_json: Path,
    network_json: Path,
    rainfall_json: Path,
    config_json: Path,
    out_inp: Path,
    out_manifest: Path,
) -> None:
    """Invoke build_swmm_inp.main() in-process with sys.argv patched.

    main() lets the underlying ValueError propagate uncaught on bad input
    (there is no try/except in the script) -- patching argv and calling it
    directly lets negative tests assertRaises(ValueError) directly, instead
    of parsing a subprocess traceback.
    """
    argv = [
        "build_swmm_inp.py",
        "--subcatchments-csv", str(subcatchments_csv),
        "--params-json", str(params_json),
        "--network-json", str(network_json),
        "--rainfall-json", str(rainfall_json),
        "--config-json", str(config_json),
        "--out-inp", str(out_inp),
        "--out-manifest", str(out_manifest),
    ]
    with mock.patch.object(sys, "argv", argv):
        module.main()


def _build_params_and_rainfall(work_dir: Path) -> dict[str, Path]:
    """Produce a merged params JSON + rainfall JSON + timeseries text from
    the shipped swmm-params / swmm-climate example fixtures.

    Mirrors scripts/acceptance/run_acceptance.py stages 02-06 and the
    private ``_build_params_and_climate`` helper in
    tests/test_wq_builder_smoke.py.  These pre-processing scripts are NOT
    the module under test -- build_swmm_inp.py only ever sees their
    *output* -- so subprocess is used here rather than importlib.
    """
    landuse_json = work_dir / "landuse.json"
    soil_json = work_dir / "soil.json"
    merged_json = work_dir / "merged_params.json"
    rainfall_json = work_dir / "rainfall.json"
    timeseries_txt = work_dir / "rainfall_timeseries.txt"

    steps = [
        [sys.executable, str(LANDUSE_SCRIPT),
         "--input", str(LANDUSE_CSV), "--output", str(landuse_json)],
        [sys.executable, str(SOIL_SCRIPT),
         "--input", str(SOIL_CSV), "--output", str(soil_json)],
        [sys.executable, str(MERGE_SCRIPT),
         "--landuse-json", str(landuse_json), "--soil-json", str(soil_json),
         "--output", str(merged_json)],
        [sys.executable, str(CLIMATE_SCRIPT),
         "--input", str(RAINFALL_CSV),
         "--out-json", str(rainfall_json), "--out-timeseries", str(timeseries_txt)],
    ]
    for cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, (
            f"fixture pre-processing step failed: {cmd}\nstderr:\n{result.stderr}"
        )

    return {
        "params_json": merged_json,
        "rainfall_json": rainfall_json,
        "timeseries_txt": timeseries_txt,
    }


def _extract_section(inp_text: str, section: str) -> str:
    """Return the ``[SECTION]`` block text (header through its last line).

    render_inp() joins each section's line-list with a blank line between
    blocks and no blank lines within a block, so slicing from the marker to
    the next blank line isolates exactly one section regardless of what
    precedes or follows it.
    """
    marker = f"[{section}]"
    start = inp_text.index(marker)
    end = inp_text.find("\n\n", start)
    return inp_text[start:] if end == -1 else inp_text[start:end]


def _data_lines(block: str) -> list[str]:
    """Strip the ``[SECTION]`` header and ``;;comment`` header row, leaving
    only the actual data rows of a section block."""
    return [
        line for line in block.splitlines()
        if line.strip() and not line.startswith("[") and not line.startswith(";;")
    ]


# ---------------------------------------------------------------------------
# Shared fixture base -- build the params/rainfall chain once per class
# ---------------------------------------------------------------------------


class _BuiltFixturesTestCase(unittest.TestCase):
    """Builds the shared merged-params + rainfall fixtures once per class.

    All outputs live under a tempfile.TemporaryDirectory(); nothing is ever
    written into the repo or examples/.
    """

    _tmp: tempfile.TemporaryDirectory
    _work_dir: Path
    _params_json: Path
    _rainfall_json: Path
    _module: types.ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._work_dir = Path(cls._tmp.name)
        fixtures = _build_params_and_rainfall(cls._work_dir)
        cls._params_json = fixtures["params_json"]
        cls._rainfall_json = fixtures["rainfall_json"]
        cls._module = _load_builder_module()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _new_out_dir(self, name: str) -> Path:
        out_dir = self._work_dir / name
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir


# ===========================================================================
# Happy path -- build from the skill's shipped example fixtures
# ===========================================================================


class HappyPathBuildTest(_BuiltFixturesTestCase):
    """Builds one INP from shipped examples; most tests below read that
    same build (setUpClass builds once; the determinism test builds twice
    more of its own to compare)."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        out_dir = cls._work_dir / "happy_path"
        out_dir.mkdir()
        cls._out_inp = out_dir / "model.inp"
        cls._out_manifest = out_dir / "manifest.json"
        _run_builder(
            cls._module,
            subcatchments_csv=SUBCATCHMENTS_CSV,
            params_json=cls._params_json,
            network_json=NETWORK_JSON,
            rainfall_json=cls._rainfall_json,
            config_json=CONFIG_JSON,
            out_inp=cls._out_inp,
            out_manifest=cls._out_manifest,
        )
        cls._inp_text = cls._out_inp.read_text(encoding="utf-8")
        cls._manifest = json.loads(cls._out_manifest.read_text(encoding="utf-8"))

    def test_build_writes_inp_and_manifest_artifacts(self) -> None:
        self.assertTrue(self._out_inp.exists())
        self.assertTrue(self._out_manifest.exists())
        self.assertTrue(self._inp_text.startswith("[TITLE]"))
        self.assertTrue(self._manifest.get("ok"))
        self.assertEqual(self._manifest.get("skill"), "swmm-builder")

    def test_subcatchments_section_populated(self) -> None:
        rows = {
            line.split()[0]: line.split()
            for line in _data_lines(_extract_section(self._inp_text, "SUBCATCHMENTS"))
        }
        self.assertEqual(set(rows), set(_EXPECTED_SUBCATCHMENT_OUTLETS))
        for subcatchment_id, outlet in _EXPECTED_SUBCATCHMENT_OUTLETS.items():
            # column order: Name RainGage Outlet Area %Imperv Width %Slope CurbLen
            self.assertEqual(
                rows[subcatchment_id][2], outlet,
                f"{subcatchment_id} outlet column mismatch: {rows[subcatchment_id]!r}",
            )
        # Spot-check one param value flowed from the merged params fixture
        # through to the emitted row (S1 pct_imperv == 85.0, derived from
        # skills/swmm-params/examples/landuse_input.csv + soil_input.csv).
        self.assertEqual(rows["S1"][4], "85")

    def test_junctions_section_populated(self) -> None:
        rows = {
            line.split()[0]: line.split()
            for line in _data_lines(_extract_section(self._inp_text, "JUNCTIONS"))
        }
        self.assertEqual(set(rows), set(_EXPECTED_JUNCTION_ELEVATIONS))
        for junction_id, elevation in _EXPECTED_JUNCTION_ELEVATIONS.items():
            # column order: Name Elevation MaxDepth InitDepth SurDepth Aponded
            self.assertEqual(rows[junction_id][1], elevation)

    def test_timeseries_section_populated(self) -> None:
        data_lines = _data_lines(_extract_section(self._inp_text, "TIMESERIES"))
        self.assertEqual(
            len(data_lines), _EXPECTED_TIMESERIES_ROWS,
            f"expected {_EXPECTED_TIMESERIES_ROWS} rows from the shipped "
            f"rainfall_event.csv fixture, got {len(data_lines)}: {data_lines!r}",
        )
        self.assertTrue(all(line.split()[0] == "TS_RAIN" for line in data_lines))

    def test_manifest_counts_match_and_validation_is_clean(self) -> None:
        counts = self._manifest["counts"]
        self.assertEqual(counts["subcatchments"], 4)
        self.assertEqual(counts["network_junctions"], 2)
        self.assertEqual(counts["network_outfalls"], 1)
        self.assertEqual(counts["network_conduits"], 2)
        self.assertEqual(counts["timeseries_rows"], _EXPECTED_TIMESERIES_ROWS)

        validation = self._manifest["validation"]
        active_issues = {k: v for k, v in validation.items() if v}
        self.assertEqual(active_issues, {}, f"unexpected validation issues: {active_issues}")

    def test_manifest_inp_sha256_matches_written_file(self) -> None:
        actual = hashlib.sha256(self._out_inp.read_bytes()).hexdigest()
        self.assertEqual(self._manifest["outputs"]["inp_sha256"], actual)

    def test_building_twice_from_same_inputs_is_byte_identical(self) -> None:
        """Guards CONTEXT.md invariant #6 (byte-level reproducibility): the
        INP text is the first artifact in that chain, so build_swmm_inp.py
        must be a pure function of its inputs with no incidental
        nondeterminism (dict ordering, timestamps, random floats, ...)."""
        dir_a = self._new_out_dir("determinism_a")
        dir_b = self._new_out_dir("determinism_b")
        inp_a, inp_b = dir_a / "model.inp", dir_b / "model.inp"

        for out_dir, out_inp in ((dir_a, inp_a), (dir_b, inp_b)):
            _run_builder(
                self._module,
                subcatchments_csv=SUBCATCHMENTS_CSV,
                params_json=self._params_json,
                network_json=NETWORK_JSON,
                rainfall_json=self._rainfall_json,
                config_json=CONFIG_JSON,
                out_inp=out_inp,
                out_manifest=out_dir / "manifest.json",
            )

        self.assertEqual(inp_a.read_bytes(), inp_b.read_bytes())


# ===========================================================================
# Negative -- out-of-range pct_imperv
# ===========================================================================


class RejectsOutOfRangePctImpervTest(unittest.TestCase):
    """[SUBCATCHMENTS] params: pct_imperv must be a percentage in [0, 100].

    Exercises validate_and_normalize_params() directly -- the function that
    enforces the bound -- rather than the full CLI, since this is a pure
    per-field check with no fixture dependency.
    """

    def setUp(self) -> None:
        self.module = _load_builder_module()

    def test_pct_imperv_above_100_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.module.validate_and_normalize_params(
                {"S1": {"pct_imperv": 150.0}}, {}, {},
            )
        message = str(ctx.exception)
        self.assertIn("pct_imperv", message)
        self.assertIn("100", message)

    def test_pct_imperv_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.module.validate_and_normalize_params(
                {"S1": {"pct_imperv": -5.0}}, {}, {},
            )
        self.assertIn("pct_imperv", str(ctx.exception))


# ===========================================================================
# Negative -- outlet referencing a nonexistent junction
# ===========================================================================


class RejectsUnknownOutletNodeTest(_BuiltFixturesTestCase):
    """A subcatchment outlet that isn't in [JUNCTIONS]/[OUTFALLS] must be
    rejected before the INP is written -- basic-network.json only defines
    J1, J2, OF1, so routing S4 to a made-up node must fail validate_ids().
    """

    _BROKEN_SUBCATCHMENTS_CSV = (
        "subcatchment_id,outlet,area_ha,width_m,slope_pct,curb_length_m\n"
        "S1,J1,2.1,150,1.2,0\n"
        "S2,J1,1.8,120,0.9,0\n"
        "S3,J2,2.4,165,1.5,0\n"
        "S4,J_GHOST,1.2,90,0.7,0\n"
    )

    def test_outlet_referencing_missing_junction_is_rejected(self) -> None:
        out_dir = self._new_out_dir("bad_outlet")
        bad_csv = out_dir / "subcatchments_bad_outlet.csv"
        bad_csv.write_text(self._BROKEN_SUBCATCHMENTS_CSV, encoding="utf-8")
        out_inp = out_dir / "model.inp"

        with self.assertRaises(ValueError) as ctx:
            _run_builder(
                self._module,
                subcatchments_csv=bad_csv,
                params_json=self._params_json,
                network_json=NETWORK_JSON,
                rainfall_json=self._rainfall_json,
                config_json=CONFIG_JSON,
                out_inp=out_inp,
                out_manifest=out_dir / "manifest.json",
            )
        message = str(ctx.exception)
        self.assertIn("missing_outlet_nodes", message)
        self.assertIn("S4", message)
        # Validation failure must short-circuit before any INP is written.
        self.assertFalse(out_inp.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
