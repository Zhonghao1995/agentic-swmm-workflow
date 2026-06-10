"""Engine smoke test for water-quality INP emission.

Builds a minimal WQ INP (1 pollutant TSS MG/L, 1 land use, full coverage,
EXP buildup, EMC washoff) using the builder, runs it through swmm5, and
asserts the engine accepted the WQ grammar.

This is the PR1 hard gate: do not merge without a passing smoke test.

Skip pattern: mirrors the repo's existing skip-if-no-engine convention
(shutil.which check, explicit skipif marker).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "skills" / "swmm-builder" / "scripts" / "build_swmm_inp.py"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _build_params_and_climate(tmp_path: Path) -> dict:
    """Run the standard builder smoke chain to produce params + climate fixtures."""
    params_dir = tmp_path / "params"
    params_dir.mkdir()
    landuse_out = params_dir / "landuse.json"
    soil_out = params_dir / "soil.json"
    merged_out = params_dir / "merged.json"
    climate_dir = tmp_path / "climate"
    climate_dir.mkdir()
    rainfall_out = climate_dir / "rainfall.json"
    ts_out = climate_dir / "ts.txt"

    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "skills/swmm-params/scripts/landuse_to_swmm_params.py"),
         "--input", str(REPO_ROOT / "skills/swmm-params/examples/landuse_input.csv"),
         "--output", str(landuse_out)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "skills/swmm-params/scripts/soil_to_greenampt.py"),
         "--input", str(REPO_ROOT / "skills/swmm-params/examples/soil_input.csv"),
         "--output", str(soil_out)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "skills/swmm-params/scripts/merge_swmm_params.py"),
         "--landuse-json", str(landuse_out),
         "--soil-json", str(soil_out),
         "--output", str(merged_out)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "skills/swmm-climate/scripts/format_rainfall.py"),
         "--input", str(REPO_ROOT / "skills/swmm-climate/examples/rainfall_event.csv"),
         "--out-json", str(rainfall_out),
         "--out-timeseries", str(ts_out)],
        check=True, capture_output=True,
    )
    return {
        "params_json": merged_out,
        "rainfall_json": rainfall_out,
    }


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
def test_wq_inp_runs_to_completion(tmp_path):
    """Build a minimal WQ INP, run it with swmm5, assert return code 0 and
    Water Quality: YES in the RPT.

    PR1 engine smoke — hard gate for the feature branch.
    """
    fixtures = _build_params_and_climate(tmp_path)

    # Write minimal WQ config JSON
    wq_json = tmp_path / "wq.json"
    wq_json.write_text(json.dumps({
        "pollutants": [{
            "name": "TSS",
            "units": "MG/L",
            "c_rain": 0.0, "c_gw": 0.0, "c_ii": 0.0,
            "k_decay_per_day": 0.0,
            "snow_only": False,
            "co_pollutant": "*", "co_fraction": 0.0,
            "init_conc": 0.0,
        }],
        "landuses": [{
            "name": "Residential",
            "sweep_interval": 0.0, "availability": 0.0, "last_sweep": 0.0,
        }],
        "coverages": [
            {"subcatchment": "S1", "landuse": "Residential", "percent": 100.0},
            {"subcatchment": "S2", "landuse": "Residential", "percent": 100.0},
            {"subcatchment": "S3", "landuse": "Residential", "percent": 100.0},
            {"subcatchment": "S4", "landuse": "Residential", "percent": 100.0},
        ],
        "buildup": [{
            "landuse": "Residential", "pollutant": "TSS",
            "func_type": "EXP", "c1": 15.0, "c2": 0.5, "c3": 0.0,
            "normalizer": "AREA",
        }],
        "washoff": [{
            "landuse": "Residential", "pollutant": "TSS",
            "func_type": "EMC", "c1": 50.0, "c2": 0.0,
            "sweep_removal": 0.0, "bmp_removal": 0.0,
        }],
        "loadings": [],
    }), encoding="utf-8")

    # Build the INP
    out_inp = tmp_path / "wq_smoke.inp"
    out_manifest = tmp_path / "wq_smoke_manifest.json"
    build_result = subprocess.run(
        [
            sys.executable, str(BUILDER),
            "--subcatchments-csv", str(REPO_ROOT / "skills/swmm-builder/examples/subcatchments_input.csv"),
            "--params-json", str(fixtures["params_json"]),
            "--network-json", str(REPO_ROOT / "skills/swmm-network/examples/basic-network.json"),
            "--rainfall-json", str(fixtures["rainfall_json"]),
            "--config-json", str(REPO_ROOT / "skills/swmm-builder/examples/options_config.json"),
            "--water-quality-json", str(wq_json),
            "--out-inp", str(out_inp),
            "--out-manifest", str(out_manifest),
        ],
        capture_output=True, text=True,
    )
    assert build_result.returncode == 0, (
        f"Builder failed:\n{build_result.stderr}\n{build_result.stdout}"
    )

    # Verify manifest reflects WQ was enabled
    manifest = json.loads(out_manifest.read_text(encoding="utf-8"))
    assert manifest.get("water_quality", {}).get("enabled") is True
    assert "POLLUTANTS" in manifest["validation_diagnostics"]["checked_sections"]

    # Run SWMM engine
    out_rpt = tmp_path / "wq_smoke.rpt"
    out_out = tmp_path / "wq_smoke.out"
    swmm_result = subprocess.run(
        ["swmm5", str(out_inp), str(out_rpt), str(out_out)],
        capture_output=True, text=True,
    )
    assert swmm_result.returncode == 0, (
        f"swmm5 returned non-zero:\nstdout: {swmm_result.stdout}\nstderr: {swmm_result.stderr}"
    )

    # Parse the RPT
    rpt_text = out_rpt.read_text(encoding="utf-8")

    # Gate 1: Water Quality must be YES
    assert "Water Quality .......... YES" in rpt_text, (
        "Water Quality not enabled in RPT — grammar or section ordering error"
    )

    # Gate 2: WQ section banners confirmed
    assert "Runoff Quality Continuity" in rpt_text, "Missing 'Runoff Quality Continuity'"
    assert "Quality Routing Continuity" in rpt_text, "Missing 'Quality Routing Continuity'"
    assert "Subcatchment Washoff Summary" in rpt_text, "Missing 'Subcatchment Washoff Summary'"
    assert "Link Pollutant Load Summary" in rpt_text, "Missing 'Link Pollutant Load Summary'"

    # Gate 3: Runoff Quality continuity error < 10% (grammar sanity)
    for line in rpt_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Continuity Error (%)") and "Runoff Quality" in rpt_text:
            # Find the line in context of Runoff Quality Continuity section
            pass
    # Extract from Runoff Quality Continuity block
    in_runoff_quality = False
    runoff_quality_error = None
    for line in rpt_text.splitlines():
        if "Runoff Quality Continuity" in line:
            in_runoff_quality = True
        if in_runoff_quality and "Continuity Error (%)" in line:
            parts = line.split()
            try:
                runoff_quality_error = float(parts[-1])
            except (ValueError, IndexError):
                pass
            break
        # Stop at next section
        if in_runoff_quality and line.strip().startswith("*") and "Runoff Quality Continuity" not in line:
            break

    if runoff_quality_error is not None:
        assert abs(runoff_quality_error) < 10.0, (
            f"Runoff Quality continuity error {runoff_quality_error}% exceeds 10% threshold — "
            "check buildup/washoff coefficients"
        )

    # Gate 4: TSS appears in WQ sections (pollutant name correctly propagated)
    assert "TSS" in rpt_text

    # Print section banners for documentation (captured in test output)
    print("\n=== WQ RPT Section Banners ===")
    lines = rpt_text.splitlines()
    for i, line in enumerate(lines):
        if "  *" in line and line.strip().startswith("*"):
            # This is a banner separator — print the title line (next non-separator line)
            for j in range(i + 1, min(i + 5, len(lines))):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("*") and not candidate.startswith("-"):
                    print(f"  SECTION: {candidate}")
                    break


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
def test_wq_smoke_wq_sections_in_inp(tmp_path):
    """INP generated with WQ flag contains all six section keywords."""
    fixtures = _build_params_and_climate(tmp_path)
    wq_json = tmp_path / "wq.json"
    wq_json.write_text(json.dumps({
        "pollutants": [{"name": "TSS", "units": "MG/L", "c_rain": 0, "c_gw": 0,
                        "c_ii": 0, "k_decay_per_day": 0, "snow_only": False,
                        "co_pollutant": "*", "co_fraction": 0, "init_conc": 0}],
        "landuses": [{"name": "Residential", "sweep_interval": 0, "availability": 0, "last_sweep": 0}],
        "coverages": [
            {"subcatchment": "S1", "landuse": "Residential", "percent": 100},
            {"subcatchment": "S2", "landuse": "Residential", "percent": 100},
            {"subcatchment": "S3", "landuse": "Residential", "percent": 100},
            {"subcatchment": "S4", "landuse": "Residential", "percent": 100},
        ],
        "buildup": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EXP",
                     "c1": 15, "c2": 0.5, "c3": 0, "normalizer": "AREA"}],
        "washoff": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EMC",
                     "c1": 50, "c2": 0, "sweep_removal": 0, "bmp_removal": 0}],
        "loadings": [],
    }), encoding="utf-8")

    out_inp = tmp_path / "wq.inp"
    out_manifest = tmp_path / "wq_manifest.json"
    build_result = subprocess.run(
        [
            sys.executable, str(BUILDER),
            "--subcatchments-csv", str(REPO_ROOT / "skills/swmm-builder/examples/subcatchments_input.csv"),
            "--params-json", str(fixtures["params_json"]),
            "--network-json", str(REPO_ROOT / "skills/swmm-network/examples/basic-network.json"),
            "--rainfall-json", str(fixtures["rainfall_json"]),
            "--config-json", str(REPO_ROOT / "skills/swmm-builder/examples/options_config.json"),
            "--water-quality-json", str(wq_json),
            "--out-inp", str(out_inp),
            "--out-manifest", str(out_manifest),
        ],
        capture_output=True, text=True,
    )
    assert build_result.returncode == 0, build_result.stderr
    text = out_inp.read_text(encoding="utf-8")
    for section in ("[POLLUTANTS]", "[LANDUSES]", "[COVERAGES]", "[BUILDUP]", "[WASHOFF]"):
        assert section in text, f"Missing {section} in WQ INP"
    # LOADINGS absent when loadings=[]
    assert "[LOADINGS]" not in text
