"""Lock-in tests for the 3 untested skills/swmm-params/scripts/*.py mappers (issue #235).

Why
---
``skills/swmm-params`` maps land-use class / soil texture to SWMM runoff,
subarea and Green-Ampt infiltration parameters via a deterministic CSV
lookup.  When an input key (a ``landuse_class`` or ``soil_texture`` value)
has no matching row in the lookup table, the mapper scripts silently
substitute the lookup's ``DEFAULT`` (or ``-``) row instead of the real
parameters for that key.

This is a documented cold-start hazard, not a hypothetical one: a field
validation run recorded exactly this failure mode --
``docs/framework-validation/saanich-smoke-20260513/cold_start_diagnostic.md``
reports that ``unmatched_landuse_classes: ["Industrial", "Single Family"]``
came back from a real run and a cold-start agent that doesn't parse that
field would never notice it got substituted (wrong) parameters instead of
real ones. There is no separate runtime warning -- the *only* trace of the
substitution is the ``unmatched_*`` list and the per-record ``used_default``
flag inside the output JSON.  ``--strict`` exists precisely so an auditable
production run can turn that silent substitution into a hard failure
instead.

These tests lock in that fallback-vs-strict contrast for all three scripts
in the skill, plus the two branches that sit on either side of it:

1. fallback branch (no ``--strict``): unmatched key -> DEFAULT/`-` row
   substituted, surfaced only via ``unmatched_*`` + ``used_default``, no
   textual warning anywhere.
2. ``--strict`` branch: the same unmatched key instead fails the process.
3. unmatched-key branch: the reported ``unmatched_*`` list is a
   deduplicated, sorted set of the raw values that missed the lookup, and
   never includes a value that actually matched.
4. incomplete-merge branch (``merge_swmm_params.py`` only): a subcatchment
   ID present in only one of the two source JSONs is flagged via
   ``missing_sections`` / ``incomplete_ids`` when not strict, and fails the
   process under ``--strict``.

Entry points under test
------------------------
- skills/swmm-params/scripts/landuse_to_swmm_params.py  (main)
- skills/swmm-params/scripts/soil_to_greenampt.py       (main)
- skills/swmm-params/scripts/merge_swmm_params.py       (main)

Loading strategy
-----------------
The three scripts are agentic_swmm-import-free stdlib CLIs (argparse + csv
+ json only), so each is loaded straight from its file path via
``importlib.util.spec_from_file_location`` -- the pattern
``tests/test_rpt_parser_parity.py`` uses (``_load_file_module``, ~line 220)
for skill scripts that live outside the importable ``agentic_swmm``
package.  ``main()`` is then called in-process with ``sys.argv`` patched
for every branch above except one: the ``--strict`` *process exit code*.
None of the three scripts wrap ``main()`` in a try/except, so a raised
``ValueError`` only becomes "exit code 1" via the Python interpreter's
default uncaught-exception handling at the ``__main__`` boundary --
calling ``main()`` in-process would just hand that same ``ValueError`` to
``assertRaises``, never producing an observable "exit code" the way a
shell/orchestrating caller would see one.  So the ``--strict`` branches
below spawn a real subprocess with ``sys.executable`` instead, and assert
on ``returncode`` and ``stderr``.

All fixtures are tiny, hand-written CSV/JSON files inside a fresh
``tempfile.TemporaryDirectory()`` per test; nothing is written into the
repo and the bundled reference lookup tables under
``skills/swmm-params/references/`` are never touched.

Run with:
    python3.11 -m pytest tests/test_swmm_params_scripts.py -v
"""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-params" / "scripts"
LANDUSE_SCRIPT = SCRIPTS_DIR / "landuse_to_swmm_params.py"
SOIL_SCRIPT = SCRIPTS_DIR / "soil_to_greenampt.py"
MERGE_SCRIPT = SCRIPTS_DIR / "merge_swmm_params.py"


# ---------------------------------------------------------------------------
# Module loaders (mirrors tests/test_rpt_parser_parity.py:_load_file_module)
# ---------------------------------------------------------------------------


def _load_file_module(name: str, path: Path):
    """Load a skill script as a module by absolute path, isolated from sys.modules.

    The module is registered in sys.modules under ``name`` before execution,
    then any pre-existing entry under ``name`` is restored afterwards so
    test isolation is preserved -- same contract as the reference loader in
    tests/test_rpt_parser_parity.py.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    prev = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev
    return module


def _load_landuse_module():
    return _load_file_module("swmm_params_landuse_under_test", LANDUSE_SCRIPT)


def _load_soil_module():
    return _load_file_module("swmm_params_soil_under_test", SOIL_SCRIPT)


def _load_merge_module():
    return _load_file_module("swmm_params_merge_under_test", MERGE_SCRIPT)


# ---------------------------------------------------------------------------
# Fixture / invocation helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _run_main(module, argv: list[str]) -> str:
    """Call ``module.main()`` in-process with ``sys.argv`` patched.

    Returns whatever main() printed to stdout (the scripts' own one-line
    JSON summary) so a test can assert on both the on-disk output file and
    the terminal-facing text a caller would see. ``argv[0]`` is a throwaway
    program-name placeholder; argparse only consumes ``sys.argv[1:]``.
    """
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
        module.main()
    return buf.getvalue()


def _run_cli(script: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Spawn the script as a real subprocess.

    Reserved for the one thing in-process module loading cannot observe:
    the process exit code an orchestrating shell would see (see module
    docstring "Loading strategy").
    """
    return subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# landuse_to_swmm_params.py
# ---------------------------------------------------------------------------


class TestLanduseToSwmmParams(unittest.TestCase):
    """skills/swmm-params/scripts/landuse_to_swmm_params.py."""

    LOOKUP_FIELDNAMES = [
        "landuse_class", "imperv_pct", "n_imperv", "n_perv",
        "dstore_imperv_in", "dstore_perv_in", "zero_imperv_pct",
        "route_to", "pct_routed", "notes",
    ]
    INPUT_FIELDNAMES = ["subcatchment_id", "landuse_class"]

    def setUp(self) -> None:
        self.module = _load_landuse_module()

    def _write_fixture(
        self, tmp: Path, input_rows: list[dict[str, str]]
    ) -> tuple[Path, Path, Path]:
        """Write a 2-row lookup (Commercial=known, DEFAULT=fallback-marker)
        plus an input CSV built from ``input_rows``.

        The DEFAULT row's numbers (imperv_pct=11, ...) are deliberately far
        from Commercial's (imperv_pct=85, ...) so a test can tell "matched
        Commercial" and "fell back to DEFAULT" apart purely from the
        numeric payload, without depending on the bundled reference lookup.
        """
        lookup_csv = tmp / "lookup.csv"
        _write_csv(lookup_csv, self.LOOKUP_FIELDNAMES, [
            {
                "landuse_class": "Commercial", "imperv_pct": "85",
                "n_imperv": "0.013", "n_perv": "0.25",
                "dstore_imperv_in": "0.05", "dstore_perv_in": "0.10",
                "zero_imperv_pct": "30", "route_to": "OUTLET",
                "pct_routed": "100", "notes": "known",
            },
            {
                "landuse_class": "DEFAULT", "imperv_pct": "11",
                "n_imperv": "0.021", "n_perv": "0.31",
                "dstore_imperv_in": "0.06", "dstore_perv_in": "0.13",
                "zero_imperv_pct": "12", "route_to": "OUTLET",
                "pct_routed": "100", "notes": "fallback-marker",
            },
        ])
        input_csv = tmp / "input.csv"
        _write_csv(input_csv, self.INPUT_FIELDNAMES, input_rows)
        output_json = tmp / "output.json"
        return lookup_csv, input_csv, output_json

    def test_fallback_branch_substitutes_default_and_reports_unmatched(self) -> None:
        """No --strict: an unmapped landuse_class ("Swampland") gets
        DEFAULT's numbers instead of erroring, and the only trace is
        unmatched_landuse_classes + used_default -- never a printed
        warning.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "landuse_class": "Commercial"},
                {"subcatchment_id": "S2", "landuse_class": "Swampland"},
            ])

            stdout = _run_main(self.module, [
                "landuse_to_swmm_params.py",
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
            ])

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["counts"],
                {"input_rows": 2, "mapped_rows": 2, "used_default_rows": 1},
            )
            self.assertEqual(payload["unmatched_landuse_classes"], ["Swampland"])

            matched, fell_back = payload["records"]
            self.assertFalse(matched["used_default"])
            self.assertEqual(matched["subcatchment"]["pct_imperv"], 85.0)

            self.assertTrue(fell_back["used_default"])
            self.assertEqual(fell_back["input_landuse_class"], "Swampland")
            self.assertEqual(fell_back["lookup_landuse_class"], "DEFAULT")
            self.assertEqual(fell_back["subcatchment"]["pct_imperv"], 11.0)
            self.assertEqual(fell_back["subarea"]["n_imperv"], 0.021)

            # Locked-in actual behaviour: the fallback is silent. The
            # printed summary repeats the same structured field (asserted
            # below) but contains no human-readable "warning" text -- an
            # agent that doesn't parse unmatched_landuse_classes will not
            # notice it received substituted parameters (cold-start
            # hazard; see module docstring).
            summary = json.loads(stdout)
            self.assertEqual(summary["unmatched_landuse_classes"], ["Swampland"])
            self.assertEqual(summary["used_default_rows"], 1)
            self.assertNotIn("warn", stdout.lower())

    def test_strict_branch_exits_nonzero_instead_of_falling_back(self) -> None:
        """Same fixture as the fallback test above, plus --strict: the
        process must fail loudly instead of silently substituting DEFAULT,
        and must not write a partial output file.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "landuse_class": "Commercial"},
                {"subcatchment_id": "S2", "landuse_class": "Swampland"},
            ])

            result = _run_cli(LANDUSE_SCRIPT, [
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
                "--strict",
            ])

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unmapped landuse_class", result.stderr)
            self.assertIn("Swampland", result.stderr)
            self.assertFalse(output_json.exists())

    def test_unmatched_key_branch_deduplicates_and_excludes_matched_keys(self) -> None:
        """unmatched_landuse_classes must be the deduplicated, sorted set
        of raw input values that missed the lookup: "Swampland" appears
        twice in the input but once in the list, "Tundra" is a distinct
        second miss, and "Commercial" (a real match) never appears even
        though it would sort first alphabetically.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "landuse_class": "Commercial"},
                {"subcatchment_id": "S2", "landuse_class": "Swampland"},
                {"subcatchment_id": "S3", "landuse_class": "Swampland"},
                {"subcatchment_id": "S4", "landuse_class": "Tundra"},
            ])

            _run_main(self.module, [
                "landuse_to_swmm_params.py",
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
            ])

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["unmatched_landuse_classes"], ["Swampland", "Tundra"]
            )
            self.assertEqual(payload["counts"]["used_default_rows"], 3)
            self.assertNotIn("Commercial", payload["unmatched_landuse_classes"])


# ---------------------------------------------------------------------------
# soil_to_greenampt.py
# ---------------------------------------------------------------------------


class TestSoilToGreenampt(unittest.TestCase):
    """skills/swmm-params/scripts/soil_to_greenampt.py."""

    LOOKUP_FIELDNAMES = ["texture", "suction_mm", "ksat_mm_per_hr", "imdmax", "notes"]
    INPUT_FIELDNAMES = ["subcatchment_id", "soil_texture"]

    def setUp(self) -> None:
        self.module = _load_soil_module()

    def _write_fixture(
        self, tmp: Path, input_rows: list[dict[str, str]]
    ) -> tuple[Path, Path, Path]:
        """Write a 2-row lookup ("sandy loam"=known, "-"=fallback-marker,
        matching the real bundled convention where "-" is the soil
        fallback key) plus an input CSV built from ``input_rows``.
        """
        lookup_csv = tmp / "lookup.csv"
        _write_csv(lookup_csv, self.LOOKUP_FIELDNAMES, [
            {
                "texture": "sandy loam", "suction_mm": "90",
                "ksat_mm_per_hr": "13", "imdmax": "0.30", "notes": "known",
            },
            {
                "texture": "-", "suction_mm": "999",
                "ksat_mm_per_hr": "999", "imdmax": "0.99",
                "notes": "fallback-marker",
            },
        ])
        input_csv = tmp / "input.csv"
        _write_csv(input_csv, self.INPUT_FIELDNAMES, input_rows)
        output_json = tmp / "output.json"
        return lookup_csv, input_csv, output_json

    def test_fallback_branch_substitutes_default_and_reports_unmatched(self) -> None:
        """No --strict: an unmapped soil_texture ("clay loam") gets the
        "-" row's Green-Ampt numbers instead of erroring, surfaced only via
        unmatched_soil_textures + used_default.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "soil_texture": "sandy loam"},
                {"subcatchment_id": "S2", "soil_texture": "clay loam"},
            ])

            stdout = _run_main(self.module, [
                "soil_to_greenampt.py",
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
            ])

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["counts"],
                {"input_rows": 2, "mapped_rows": 2, "used_default_rows": 1},
            )
            self.assertEqual(payload["unmatched_soil_textures"], ["clay loam"])

            matched, fell_back = payload["records"]
            self.assertFalse(matched["used_default"])
            self.assertEqual(matched["infiltration"]["suction_mm"], 90.0)

            self.assertTrue(fell_back["used_default"])
            self.assertEqual(fell_back["input_soil_texture"], "clay loam")
            self.assertEqual(fell_back["lookup_texture"], "-")
            self.assertEqual(fell_back["infiltration"]["suction_mm"], 999.0)
            self.assertEqual(fell_back["infiltration"]["ksat_mm_per_hr"], 999.0)
            self.assertEqual(fell_back["infiltration"]["imdmax"], 0.99)

            # Same silent-fallback contract as the landuse mapper: no
            # textual warning, only the structured field.
            summary = json.loads(stdout)
            self.assertEqual(summary["unmatched_soil_textures"], ["clay loam"])
            self.assertEqual(summary["used_default_rows"], 1)
            self.assertNotIn("warn", stdout.lower())

    def test_strict_branch_exits_nonzero_instead_of_falling_back(self) -> None:
        """Same fixture as the fallback test above, plus --strict: the
        process must fail instead of silently substituting the "-" row,
        and must not write a partial output file.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "soil_texture": "sandy loam"},
                {"subcatchment_id": "S2", "soil_texture": "clay loam"},
            ])

            result = _run_cli(SOIL_SCRIPT, [
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
                "--strict",
            ])

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unmapped soil texture", result.stderr)
            self.assertIn("clay loam", result.stderr)
            self.assertFalse(output_json.exists())

    def test_unmatched_key_branch_deduplicates_and_excludes_matched_keys(self) -> None:
        """unmatched_soil_textures must be the deduplicated, sorted set of
        raw input values that missed the lookup: "clay loam" appears twice
        in the input but once in the list, "peat" is a distinct second
        miss, and "sandy loam" (a real match) never appears.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            lookup_csv, input_csv, output_json = self._write_fixture(tmp, [
                {"subcatchment_id": "S1", "soil_texture": "sandy loam"},
                {"subcatchment_id": "S2", "soil_texture": "clay loam"},
                {"subcatchment_id": "S3", "soil_texture": "clay loam"},
                {"subcatchment_id": "S4", "soil_texture": "peat"},
            ])

            _run_main(self.module, [
                "soil_to_greenampt.py",
                "--input", str(input_csv),
                "--lookup", str(lookup_csv),
                "--output", str(output_json),
            ])

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["unmatched_soil_textures"], ["clay loam", "peat"]
            )
            self.assertEqual(payload["counts"]["used_default_rows"], 3)
            self.assertNotIn("sandy loam", payload["unmatched_soil_textures"])


# ---------------------------------------------------------------------------
# merge_swmm_params.py
# ---------------------------------------------------------------------------


class TestMergeSwmmParams(unittest.TestCase):
    """skills/swmm-params/scripts/merge_swmm_params.py."""

    def setUp(self) -> None:
        self.module = _load_merge_module()

    def _write_sources(self, tmp: Path) -> tuple[Path, Path]:
        """Hand-craft a landuse-mapper-shaped JSON and a soil-mapper-shaped
        JSON whose ``sections`` overlap on S1 (complete), but each has one
        ID the other lacks: S2 is landuse-only (no infiltration), S3 is
        soil-only (no subcatchment/subarea).  This directly targets
        ``index_by_id`` + the missing-sections bookkeeping in ``main()``
        without depending on landuse_to_swmm_params.py / soil_to_greenampt.py
        actually running first.
        """
        landuse_json = tmp / "landuse.json"
        _write_json(landuse_json, {
            "ok": True,
            "sections": {
                "subcatchments": [
                    {"id": "S1", "pct_imperv": 85.0},
                    {"id": "S2", "pct_imperv": 11.0},
                ],
                "subareas": [
                    {"id": "S1", "n_imperv": 0.013},
                    {"id": "S2", "n_imperv": 0.021},
                ],
            },
        })
        soil_json = tmp / "soil.json"
        _write_json(soil_json, {
            "ok": True,
            "sections": {
                "infiltration": [
                    {"id": "S1", "suction_mm": 90.0},
                    {"id": "S3", "suction_mm": 999.0},
                ],
            },
        })
        return landuse_json, soil_json

    def test_incomplete_merge_branch_flags_missing_sections_without_strict(self) -> None:
        """No --strict: S2 (missing infiltration) and S3 (missing
        subcatchment/subarea) are flagged via missing_sections /
        incomplete_ids instead of failing; S1 (complete) is the contrasting
        control case and must show up clean.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            landuse_json, soil_json = self._write_sources(tmp)
            output_json = tmp / "merged.json"

            _run_main(self.module, [
                "merge_swmm_params.py",
                "--landuse-json", str(landuse_json),
                "--soil-json", str(soil_json),
                "--output", str(output_json),
            ])

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["subcatchment_count"], 3)
            self.assertEqual(payload["counts"]["incomplete_subcatchment_count"], 2)

            incomplete_by_id = {
                d["id"]: d["missing_sections"] for d in payload["incomplete_ids"]
            }
            self.assertEqual(
                incomplete_by_id,
                {"S2": ["infiltration"], "S3": ["subcatchments", "subareas"]},
            )

            by_id = {r["id"]: r for r in payload["by_subcatchment"]}

            # S1: complete -- no missing_sections key, all three present.
            self.assertNotIn("missing_sections", by_id["S1"])
            self.assertIn("subcatchment", by_id["S1"])
            self.assertIn("subarea", by_id["S1"])
            self.assertIn("infiltration", by_id["S1"])

            # S2: landuse-only -- flagged, infiltration absent.
            self.assertEqual(by_id["S2"]["missing_sections"], ["infiltration"])
            self.assertIn("subcatchment", by_id["S2"])
            self.assertIn("subarea", by_id["S2"])
            self.assertNotIn("infiltration", by_id["S2"])

            # S3: soil-only -- flagged, subcatchment/subarea absent.
            self.assertEqual(
                by_id["S3"]["missing_sections"], ["subcatchments", "subareas"]
            )
            self.assertNotIn("subcatchment", by_id["S3"])
            self.assertNotIn("subarea", by_id["S3"])
            self.assertIn("infiltration", by_id["S3"])

    def test_incomplete_merge_branch_strict_exits_nonzero(self) -> None:
        """Same fixture as above, plus --strict: the process must fail
        instead of silently emitting a merged file with incomplete
        entries, and must not write a partial output file.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            landuse_json, soil_json = self._write_sources(tmp)
            output_json = tmp / "merged.json"

            result = _run_cli(MERGE_SCRIPT, [
                "--landuse-json", str(landuse_json),
                "--soil-json", str(soil_json),
                "--output", str(output_json),
                "--strict",
            ])

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("incomplete subcatchment mappings", result.stderr)
            self.assertIn("S2", result.stderr)
            self.assertIn("S3", result.stderr)
            self.assertFalse(output_json.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
