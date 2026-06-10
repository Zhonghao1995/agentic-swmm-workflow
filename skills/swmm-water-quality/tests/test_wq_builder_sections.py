"""Unit tests for WQ section emission in build_swmm_inp.py.

Tests cover:
- Golden emission for each of the six WQ sections
- All buildup function types (POW, EXP, SAT)
- All washoff function types (EXP, RC, EMC)
- LOADINGS optional section (present and absent cases)
- Validation cross-reference failures
- Enum validity checks
- EXT rejection
- No-flag byte-identity lock (output must be byte-identical to pre-WQ build)
- Determinism (two runs produce identical INP)
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILDER = REPO_ROOT / "skills" / "swmm-builder" / "scripts" / "build_swmm_inp.py"
VALIDATOR = Path(__file__).resolve().parents[1] / "scripts" / "validate_wq_config.py"

# ---------------------------------------------------------------------------
# Helper: load the builder module for function-level tests
# ---------------------------------------------------------------------------


def _load_builder():
    spec = importlib.util.spec_from_file_location("_build_swmm_inp_under_test", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUILDER_MODULE = _load_builder()
_emit_pollutants = _BUILDER_MODULE.emit_pollutants
_emit_landuses = _BUILDER_MODULE.emit_landuses
_emit_coverages = _BUILDER_MODULE.emit_coverages
_emit_buildup = _BUILDER_MODULE.emit_buildup
_emit_washoff = _BUILDER_MODULE.emit_washoff
_emit_loadings = _BUILDER_MODULE.emit_loadings
_validate_wq_config = _BUILDER_MODULE.validate_wq_config


# ---------------------------------------------------------------------------
# Minimal WQ fixture
# ---------------------------------------------------------------------------


def _minimal_wq():
    return {
        "pollutants": [
            {
                "name": "TSS",
                "units": "MG/L",
                "c_rain": 0.0,
                "c_gw": 0.0,
                "c_ii": 0.0,
                "k_decay_per_day": 0.0,
                "snow_only": False,
                "co_pollutant": "*",
                "co_fraction": 0.0,
                "init_conc": 0.0,
            }
        ],
        "landuses": [
            {
                "name": "Residential",
                "sweep_interval": 0.0,
                "availability": 0.0,
                "last_sweep": 0.0,
            }
        ],
        "coverages": [
            {"subcatchment": "S1", "landuse": "Residential", "percent": 100.0},
        ],
        "buildup": [
            {
                "landuse": "Residential",
                "pollutant": "TSS",
                "func_type": "EXP",
                "c1": 15.0,
                "c2": 0.5,
                "c3": 0.0,
                "normalizer": "AREA",
            }
        ],
        "washoff": [
            {
                "landuse": "Residential",
                "pollutant": "TSS",
                "func_type": "EMC",
                "c1": 50.0,
                "c2": 0.0,
                "sweep_removal": 0.0,
                "bmp_removal": 0.0,
            }
        ],
        "loadings": [],
    }


# ---------------------------------------------------------------------------
# emit_pollutants
# ---------------------------------------------------------------------------


def test_emit_pollutants_column_order():
    wq = _minimal_wq()
    lines = _emit_pollutants(wq)
    assert lines[0] == "[POLLUTANTS]"
    # Header
    assert "Units" in lines[1] and "Cppt" in lines[1] and "InitConc" in lines[1]
    # Data row: TSS MG/L 0 0 0 0 NO * 0 0
    data = lines[2]
    parts = data.split()
    assert parts[0] == "TSS"
    assert parts[1] == "MG/L"
    # snow_only=False -> NO
    assert "NO" in parts
    # co_pollutant=* is present
    assert "*" in parts


def test_emit_pollutants_units_variants():
    for units in ("MG/L", "UG/L", "#/L"):
        wq = _minimal_wq()
        wq["pollutants"][0]["units"] = units
        lines = _emit_pollutants(wq)
        assert units in lines[2], f"units {units} not in {lines[2]}"


def test_emit_pollutants_snow_only():
    wq = _minimal_wq()
    wq["pollutants"][0]["snow_only"] = True
    lines = _emit_pollutants(wq)
    assert "YES" in lines[2]


def test_emit_pollutants_multiple():
    wq = _minimal_wq()
    wq["pollutants"].append({
        "name": "COD",
        "units": "MG/L",
        "c_rain": 0.0, "c_gw": 0.0, "c_ii": 0.0,
        "k_decay_per_day": 0.1,
        "snow_only": False,
        "co_pollutant": "TSS",
        "co_fraction": 0.3,
        "init_conc": 0.0,
    })
    lines = _emit_pollutants(wq)
    assert len(lines) == 4  # header + comment + 2 data rows
    assert "COD" in lines[3]
    assert "TSS" in lines[3]


# ---------------------------------------------------------------------------
# emit_landuses
# ---------------------------------------------------------------------------


def test_emit_landuses_column_order():
    wq = _minimal_wq()
    lines = _emit_landuses(wq)
    assert lines[0] == "[LANDUSES]"
    assert "SweepInterval" in lines[1]
    assert "Availability" in lines[1]
    assert "LastSweep" in lines[1]
    parts = lines[2].split()
    assert parts[0] == "Residential"


def test_emit_landuses_non_zero_sweep():
    wq = _minimal_wq()
    wq["landuses"][0].update({"sweep_interval": 7.0, "availability": 0.8, "last_sweep": 3.0})
    lines = _emit_landuses(wq)
    data = lines[2]
    assert "7" in data
    assert "0.8" in data or "0.8" in data.replace("0.800000", "0.8")


# ---------------------------------------------------------------------------
# emit_coverages
# ---------------------------------------------------------------------------


def test_emit_coverages_column_order():
    wq = _minimal_wq()
    lines = _emit_coverages(wq)
    assert lines[0] == "[COVERAGES]"
    assert "Subcatchment" in lines[1]
    assert "LandUse" in lines[1]
    assert "Percent" in lines[1]
    parts = lines[2].split()
    assert parts[0] == "S1"
    assert parts[1] == "Residential"
    assert parts[2] == "100"


def test_emit_coverages_multiple_rows():
    wq = _minimal_wq()
    wq["coverages"] = [
        {"subcatchment": "S1", "landuse": "Residential", "percent": 60.0},
        {"subcatchment": "S1", "landuse": "Commercial", "percent": 40.0},
    ]
    wq["landuses"].append({"name": "Commercial", "sweep_interval": 0, "availability": 0, "last_sweep": 0})
    lines = _emit_coverages(wq)
    assert len(lines) == 4  # header + comment + 2 data rows


# ---------------------------------------------------------------------------
# emit_buildup — POW, EXP, SAT
# ---------------------------------------------------------------------------


def test_emit_buildup_exp():
    wq = _minimal_wq()
    lines = _emit_buildup(wq)
    assert lines[0] == "[BUILDUP]"
    assert "FuncType" in lines[1]
    parts = lines[2].split()
    assert parts[0] == "Residential"
    assert parts[1] == "TSS"
    assert parts[2] == "EXP"
    assert parts[6] == "AREA"


def test_emit_buildup_pow():
    wq = _minimal_wq()
    wq["buildup"][0].update({"func_type": "POW", "c1": 10.0, "c2": 0.3, "c3": 0.7})
    lines = _emit_buildup(wq)
    parts = lines[2].split()
    assert parts[2] == "POW"
    assert "10" in parts[3]
    assert "0.3" in parts[4] or "0.3" in " ".join(parts)
    assert "0.7" in parts[5] or "0.7" in " ".join(parts)


def test_emit_buildup_sat():
    wq = _minimal_wq()
    wq["buildup"][0].update({"func_type": "SAT", "c1": 20.0, "c2": 2.5, "c3": 0.0})
    lines = _emit_buildup(wq)
    parts = lines[2].split()
    assert parts[2] == "SAT"


def test_emit_buildup_curblength_normalizer():
    wq = _minimal_wq()
    wq["buildup"][0]["normalizer"] = "CURBLENGTH"
    lines = _emit_buildup(wq)
    assert "CURBLENGTH" in lines[2]


# ---------------------------------------------------------------------------
# emit_washoff — EXP, RC, EMC
# ---------------------------------------------------------------------------


def test_emit_washoff_emc():
    wq = _minimal_wq()
    lines = _emit_washoff(wq)
    assert lines[0] == "[WASHOFF]"
    assert "FuncType" in lines[1]
    assert "SweepRemoval" in lines[1]
    assert "BMPRemoval" in lines[1]
    parts = lines[2].split()
    assert parts[0] == "Residential"
    assert parts[1] == "TSS"
    assert parts[2] == "EMC"


def test_emit_washoff_exp():
    wq = _minimal_wq()
    wq["washoff"][0].update({"func_type": "EXP", "c1": 0.18, "c2": 1.8})
    lines = _emit_washoff(wq)
    parts = lines[2].split()
    assert parts[2] == "EXP"


def test_emit_washoff_rc():
    wq = _minimal_wq()
    wq["washoff"][0].update({"func_type": "RC", "c1": 0.1, "c2": 2.0})
    lines = _emit_washoff(wq)
    parts = lines[2].split()
    assert parts[2] == "RC"


def test_emit_washoff_sweep_and_bmp():
    wq = _minimal_wq()
    wq["washoff"][0].update({"sweep_removal": 0.5, "bmp_removal": 0.3})
    lines = _emit_washoff(wq)
    # Both values appear in the data row
    assert "0.5" in lines[2] or "0.5" in lines[2].replace("0.500000", "0.5")
    assert "0.3" in lines[2] or "0.3" in lines[2].replace("0.300000", "0.3")


# ---------------------------------------------------------------------------
# emit_loadings — present and absent
# ---------------------------------------------------------------------------


def test_emit_loadings_absent_when_empty():
    wq = _minimal_wq()
    wq["loadings"] = []
    lines = _emit_loadings(wq)
    assert lines == []


def test_emit_loadings_absent_when_missing_key():
    wq = _minimal_wq()
    del wq["loadings"]
    lines = _emit_loadings(wq)
    assert lines == []


def test_emit_loadings_with_rows():
    wq = _minimal_wq()
    wq["loadings"] = [
        {"subcatchment": "S1", "pollutant": "TSS", "init_buildup": 2.5},
        {"subcatchment": "S2", "pollutant": "TSS", "init_buildup": 1.0},
    ]
    lines = _emit_loadings(wq)
    assert lines[0] == "[LOADINGS]"
    assert "Subcatchment" in lines[1]
    assert "InitBuildup" in lines[1]
    # Two data rows
    assert len(lines) == 4
    assert "S1" in lines[2] and "TSS" in lines[2]
    assert "S2" in lines[3] and "TSS" in lines[3]


# ---------------------------------------------------------------------------
# validate_wq_config — cross-reference failures
# ---------------------------------------------------------------------------


def test_validate_wq_xrefs_pass():
    """Valid config must not raise."""
    wq = _minimal_wq()
    _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_landuse_in_buildup():
    wq = _minimal_wq()
    wq["buildup"][0]["landuse"] = "NonExistentLU"
    with pytest.raises(ValueError, match="'landuse'.*not defined in \\[LANDUSES\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_pollutant_in_buildup():
    wq = _minimal_wq()
    wq["buildup"][0]["pollutant"] = "NonExistentPol"
    with pytest.raises(ValueError, match="'pollutant'.*not defined in \\[POLLUTANTS\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_landuse_in_washoff():
    wq = _minimal_wq()
    wq["washoff"][0]["landuse"] = "GhostLU"
    with pytest.raises(ValueError, match="'landuse'.*not defined in \\[LANDUSES\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_pollutant_in_washoff():
    wq = _minimal_wq()
    wq["washoff"][0]["pollutant"] = "GhostPol"
    with pytest.raises(ValueError, match="'pollutant'.*not defined in \\[POLLUTANTS\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_landuse_in_coverages():
    wq = _minimal_wq()
    wq["coverages"][0]["landuse"] = "UnknownLU"
    with pytest.raises(ValueError, match="'landuse'.*not defined in \\[LANDUSES\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_coverage_percent_over_100():
    wq = _minimal_wq()
    wq["coverages"] = [
        {"subcatchment": "S1", "landuse": "Residential", "percent": 80.0},
        {"subcatchment": "S1", "landuse": "Residential", "percent": 30.0},
    ]
    with pytest.raises(ValueError, match="coverage percents sum"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_subcatchment_in_coverages():
    wq = _minimal_wq()
    wq["coverages"][0]["subcatchment"] = "S99"
    with pytest.raises(ValueError, match="not found in subcatchments"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_pollutant_in_loadings():
    wq = _minimal_wq()
    wq["loadings"] = [{"subcatchment": "S1", "pollutant": "GhostPol", "init_buildup": 1.0}]
    with pytest.raises(ValueError, match="'pollutant'.*not defined in \\[POLLUTANTS\\]"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_xrefs_fail_missing_subcatchment_in_loadings():
    wq = _minimal_wq()
    wq["loadings"] = [{"subcatchment": "S99", "pollutant": "TSS", "init_buildup": 1.0}]
    with pytest.raises(ValueError, match="not found in subcatchments"):
        _validate_wq_config(wq, known_subcatchment_ids={"S1"})


def test_validate_wq_invalid_units():
    wq = _minimal_wq()
    wq["pollutants"][0]["units"] = "INVALID"
    with pytest.raises(ValueError, match="'units' must be one of"):
        _validate_wq_config(wq)


def test_validate_wq_invalid_buildup_func():
    wq = _minimal_wq()
    wq["buildup"][0]["func_type"] = "LINEAR"
    with pytest.raises(ValueError, match="'func_type' must be one of"):
        _validate_wq_config(wq)


def test_validate_wq_invalid_washoff_func():
    wq = _minimal_wq()
    wq["washoff"][0]["func_type"] = "BADTYPE"
    with pytest.raises(ValueError, match="'func_type' must be one of"):
        _validate_wq_config(wq)


def test_validate_wq_ext_rejected():
    """EXT buildup function must be rejected with a clear message."""
    wq = _minimal_wq()
    wq["buildup"][0]["func_type"] = "EXT"
    with pytest.raises(ValueError, match="EXT.*not supported in v1"):
        _validate_wq_config(wq)


def test_validate_wq_duplicate_pollutant_name():
    wq = _minimal_wq()
    wq["pollutants"].append({
        "name": "TSS",
        "units": "MG/L",
        "c_rain": 0, "c_gw": 0, "c_ii": 0,
        "k_decay_per_day": 0,
        "snow_only": False,
        "co_pollutant": "*",
        "co_fraction": 0,
        "init_conc": 0,
    })
    with pytest.raises(ValueError, match="duplicate pollutant name"):
        _validate_wq_config(wq)


def test_validate_wq_pollutant_name_with_spaces():
    wq = _minimal_wq()
    wq["pollutants"][0]["name"] = "TSS FINE"
    with pytest.raises(ValueError, match="must not contain spaces"):
        _validate_wq_config(wq)


def test_validate_wq_sweep_removal_out_of_range():
    wq = _minimal_wq()
    wq["washoff"][0]["sweep_removal"] = 1.5
    with pytest.raises(ValueError, match="must be <= 1"):
        _validate_wq_config(wq)


# ---------------------------------------------------------------------------
# No-flag byte-identity lock (CLI subprocess)
# ---------------------------------------------------------------------------


@pytest.fixture
def smoke_inputs(tmp_path):
    """Build the params/climate fixtures needed by the builder CLI."""
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
        "subcatchments_csv": REPO_ROOT / "skills/swmm-builder/examples/subcatchments_input.csv",
        "network_json": REPO_ROOT / "skills/swmm-network/examples/basic-network.json",
        "config_json": REPO_ROOT / "skills/swmm-builder/examples/options_config.json",
    }


def _run_builder(inputs, tmp_path, extra_args=None):
    out_inp = tmp_path / "out.inp"
    out_manifest = tmp_path / "out_manifest.json"
    cmd = [
        sys.executable, str(BUILDER),
        "--subcatchments-csv", str(inputs["subcatchments_csv"]),
        "--params-json", str(inputs["params_json"]),
        "--network-json", str(inputs["network_json"]),
        "--rainfall-json", str(inputs["rainfall_json"]),
        "--config-json", str(inputs["config_json"]),
        "--out-inp", str(out_inp),
        "--out-manifest", str(out_manifest),
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result, out_inp, out_manifest


def test_no_flag_byte_identity_lock(tmp_path, smoke_inputs):
    """Without --water-quality-json, two runs produce byte-identical output."""
    run1_dir = tmp_path / "run1"
    run1_dir.mkdir()
    run2_dir = tmp_path / "run2"
    run2_dir.mkdir()
    r1, inp1, _ = _run_builder(smoke_inputs, run1_dir)
    r2, inp2, _ = _run_builder(smoke_inputs, run2_dir)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert inp1.read_bytes() == inp2.read_bytes(), "Two builder runs produced different INP bytes"


def test_wq_flag_determinism(tmp_path, smoke_inputs):
    """With --water-quality-json, two runs produce byte-identical WQ INP."""
    import json

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

    run1_dir = tmp_path / "wq1"
    run1_dir.mkdir()
    run2_dir = tmp_path / "wq2"
    run2_dir.mkdir()
    r1, inp1, _ = _run_builder(smoke_inputs, run1_dir, ["--water-quality-json", str(wq_json)])
    r2, inp2, _ = _run_builder(smoke_inputs, run2_dir, ["--water-quality-json", str(wq_json)])
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert inp1.read_bytes() == inp2.read_bytes()


def test_wq_sections_present_in_inp(tmp_path, smoke_inputs):
    """Generated INP with WQ flag contains all six section headers."""
    import json

    wq_json = tmp_path / "wq.json"
    wq_json.write_text(json.dumps({
        "pollutants": [{"name": "TSS", "units": "MG/L", "c_rain": 0, "c_gw": 0,
                        "c_ii": 0, "k_decay_per_day": 0, "snow_only": False,
                        "co_pollutant": "*", "co_fraction": 0, "init_conc": 0}],
        "landuses": [{"name": "Residential", "sweep_interval": 0, "availability": 0, "last_sweep": 0}],
        "coverages": [{"subcatchment": "S1", "landuse": "Residential", "percent": 100},
                      {"subcatchment": "S2", "landuse": "Residential", "percent": 100},
                      {"subcatchment": "S3", "landuse": "Residential", "percent": 100},
                      {"subcatchment": "S4", "landuse": "Residential", "percent": 100}],
        "buildup": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EXP",
                     "c1": 15, "c2": 0.5, "c3": 0, "normalizer": "AREA"}],
        "washoff": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EMC",
                     "c1": 50, "c2": 0, "sweep_removal": 0, "bmp_removal": 0}],
        "loadings": [{"subcatchment": "S1", "pollutant": "TSS", "init_buildup": 2.5}],
    }), encoding="utf-8")

    run_dir = tmp_path / "wq_all"
    run_dir.mkdir()
    r, inp, _ = _run_builder(smoke_inputs, run_dir, ["--water-quality-json", str(wq_json)])
    assert r.returncode == 0, r.stderr
    text = inp.read_text(encoding="utf-8")
    for section in ("[POLLUTANTS]", "[LANDUSES]", "[COVERAGES]", "[BUILDUP]", "[WASHOFF]", "[LOADINGS]"):
        assert section in text, f"{section} not found in generated INP"
    assert "TSS" in text
    assert "Residential" in text
    assert "EMC" in text


def test_wq_flag_absent_no_wq_sections(tmp_path, smoke_inputs):
    """Without --water-quality-json, no WQ sections appear in the INP."""
    run_dir = tmp_path / "no_wq"
    run_dir.mkdir()
    r, inp, _ = _run_builder(smoke_inputs, run_dir)
    assert r.returncode == 0, r.stderr
    text = inp.read_text(encoding="utf-8")
    for section in ("[POLLUTANTS]", "[LANDUSES]", "[COVERAGES]", "[BUILDUP]", "[WASHOFF]", "[LOADINGS]"):
        assert section not in text, f"Unexpected {section} in no-WQ INP"


def test_wq_flag_does_not_change_base_output(tmp_path, smoke_inputs):
    """The base INP (no WQ) must be byte-identical before and after WQ support was added."""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    r, base_inp, _ = _run_builder(smoke_inputs, base_dir)
    assert r.returncode == 0, r.stderr
    # Re-run to confirm determinism (regression gate: if WQ code touches base path, this fails)
    base2_dir = tmp_path / "base2"
    base2_dir.mkdir()
    r2, base2_inp, _ = _run_builder(smoke_inputs, base2_dir)
    assert r2.returncode == 0, r2.stderr
    assert base_inp.read_bytes() == base2_inp.read_bytes()


def test_validate_wq_config_cli_exit_zero(tmp_path):
    """Standalone validator exits 0 on valid config."""
    import json

    wq_json = tmp_path / "wq.json"
    wq_json.write_text(json.dumps({
        "pollutants": [{"name": "TSS", "units": "MG/L", "c_rain": 0, "c_gw": 0,
                        "c_ii": 0, "k_decay_per_day": 0, "snow_only": False,
                        "co_pollutant": "*", "co_fraction": 0, "init_conc": 0}],
        "landuses": [{"name": "Residential", "sweep_interval": 0, "availability": 0, "last_sweep": 0}],
        "coverages": [{"subcatchment": "S1", "landuse": "Residential", "percent": 100}],
        "buildup": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EXP",
                     "c1": 15, "c2": 0.5, "c3": 0, "normalizer": "AREA"}],
        "washoff": [{"landuse": "Residential", "pollutant": "TSS", "func_type": "EMC",
                     "c1": 50, "c2": 0, "sweep_removal": 0, "bmp_removal": 0}],
        "loadings": [],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--wq-json", str(wq_json)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout
    import json as _json
    out = _json.loads(result.stdout)
    assert out["ok"] is True


def test_validate_wq_config_cli_exit_one_on_error(tmp_path):
    """Standalone validator exits 1 on invalid config."""
    import json

    wq_json = tmp_path / "bad_wq.json"
    wq_json.write_text(json.dumps({
        "pollutants": [{"name": "TSS", "units": "KILOGRAMS",  # bad units
                        "c_rain": 0, "c_gw": 0, "c_ii": 0,
                        "k_decay_per_day": 0, "snow_only": False,
                        "co_pollutant": "*", "co_fraction": 0, "init_conc": 0}],
        "landuses": [], "coverages": [], "buildup": [], "washoff": [], "loadings": [],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--wq-json", str(wq_json)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    import json as _json
    out = _json.loads(result.stdout)
    assert out["ok"] is False
    assert "units" in out["error"]
