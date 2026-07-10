from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parents[1]
QGIS_SCRIPT = REPO_ROOT / "skills/swmm-gis/scripts/qgis_prepare_swmm_inputs.py"
AREA_WEIGHTED_SCRIPT = REPO_ROOT / "skills/swmm-gis/scripts/area_weighted_swmm_params.py"
QGIS_EXAMPLE = REPO_ROOT / "skills/swmm-gis/examples/qgis_overlay_subcatchments.geojson"
NETWORK_EXAMPLE = REPO_ROOT / "skills/swmm-network/examples/basic-network.json"


def test_qgis_overlay_export_produces_param_csvs(tmp_path: Path) -> None:
    landuse_csv = tmp_path / "landuse.csv"
    soil_csv = tmp_path / "soil.csv"

    proc = subprocess.run(
        [
            sys.executable,
            str(QGIS_SCRIPT),
            "overlay-landuse-soil",
            "--subcatchments-geojson",
            str(QGIS_EXAMPLE),
            "--out-landuse-csv",
            str(landuse_csv),
            "--out-soil-csv",
            str(soil_csv),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)

    assert summary["ok"] is True
    assert summary["subcatchment_count"] == 3
    with landuse_csv.open(newline="", encoding="utf-8") as f:
        landuse_rows = list(csv.DictReader(f))
    with soil_csv.open(newline="", encoding="utf-8") as f:
        soil_rows = list(csv.DictReader(f))

    assert landuse_rows == [
        {"subcatchment_id": "Q1", "landuse_class": "Rural"},
        {"subcatchment_id": "Q2", "landuse_class": "Natural Park Zone"},
        {"subcatchment_id": "Q3", "landuse_class": "Recreation and Open Space"},
    ]
    assert soil_rows == [
        {"subcatchment_id": "Q1", "soil_texture": "loam"},
        {"subcatchment_id": "Q2", "soil_texture": "sandy loam"},
        {"subcatchment_id": "Q3", "soil_texture": "silt loam"},
    ]


def test_qgis_prepare_script_exposes_normalization_command() -> None:
    text = QGIS_SCRIPT.read_text(encoding="utf-8")
    assert "normalize-layers" in text
    assert "native:reprojectlayer" in text
    assert "native:clip" in text
    assert "gdal:warpreproject" in text
    assert "gdal:cliprasterbymasklayer" in text


def test_qgis_export_swmm_intermediates_builds_standard_run_dirs(tmp_path: Path) -> None:
    run_dir = tmp_path / "qgis-demo"

    proc = subprocess.run(
        [
            sys.executable,
            str(QGIS_SCRIPT),
            "export-swmm-intermediates",
            "--case-id",
            "qgis-demo",
            "--run-dir",
            str(run_dir),
            "--subcatchments-geojson",
            str(QGIS_EXAMPLE),
            "--network-json",
            str(NETWORK_EXAMPLE),
            "--default-rain-gage",
            "RG1",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)

    assert summary["ok"] is True
    assert (run_dir / "00_raw/qgis_layers_manifest.json").exists()
    assert (run_dir / "00_raw/qgis_crs_report.json").exists()
    assert (run_dir / "01_gis/subcatchments.csv").exists()
    assert (run_dir / "01_gis/subcatchments.json").exists()
    assert (run_dir / "02_params/landuse.json").exists()
    assert (run_dir / "02_params/soil.json").exists()
    assert (run_dir / "02_params/merged_params.json").exists()
    assert (run_dir / "04_network/network.json").exists()
    assert (run_dir / "04_network/network_qa.json").exists()
    assert (run_dir / "qgis_export_manifest.json").exists()

    manifest = json.loads((run_dir / "qgis_export_manifest.json").read_text(encoding="utf-8"))
    subcatchments = list(csv.DictReader((run_dir / "01_gis/subcatchments.csv").open(newline="", encoding="utf-8")))
    merged_params = json.loads((run_dir / "02_params/merged_params.json").read_text(encoding="utf-8"))
    network_qa = json.loads((run_dir / "04_network/network_qa.json").read_text(encoding="utf-8"))

    assert manifest["adapter"] == "qgis_data_prep"
    assert manifest["stage_summaries"]["preprocess"]["subcatchment_count"] == 3
    assert {row["subcatchment_id"] for row in subcatchments} == {"Q1", "Q2", "Q3"}
    assert {row["rain_gage"] for row in subcatchments} == {"RG1"}
    assert sorted(row["id"] for row in merged_params["by_subcatchment"]) == ["Q1", "Q2", "Q3"]
    assert network_qa["ok"] is True


def test_area_weighted_swmm_params_from_polygon_intersections(tmp_path: Path) -> None:
    subcatchments = tmp_path / "subcatchments.geojson"
    landuse = tmp_path / "landuse.geojson"
    soil = tmp_path / "soil.geojson"
    out_dir = tmp_path / "weighted"

    gpd.GeoDataFrame(
        [{"basin_id": "S1", "geometry": box(0, 0, 10, 10)}, {"basin_id": "S2", "geometry": box(10, 0, 20, 10)}],
        crs="EPSG:32610",
    ).to_file(subcatchments, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {"CLASS": "Rural", "geometry": box(0, 0, 5, 10)},
            {"CLASS": "Commercial", "geometry": box(5, 0, 10, 10)},
            {"CLASS": "Natural Park Zone", "geometry": box(10, 0, 20, 10)},
        ],
        crs="EPSG:32610",
    ).to_file(landuse, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {"TEXTURE": "loam", "geometry": box(0, 0, 10, 5)},
            {"TEXTURE": "sandy loam", "geometry": box(0, 5, 10, 10)},
            {"TEXTURE": "silt loam", "geometry": box(10, 0, 20, 10)},
        ],
        crs="EPSG:32610",
    ).to_file(soil, driver="GeoJSON")

    proc = subprocess.run(
        [
            sys.executable,
            str(AREA_WEIGHTED_SCRIPT),
            "--subcatchments",
            str(subcatchments),
            "--landuse",
            str(landuse),
            "--soil",
            str(soil),
            "--out-dir",
            str(out_dir),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)
    params = json.loads((out_dir / "weighted_params.json").read_text(encoding="utf-8"))
    land_weights = list(csv.DictReader((out_dir / "landuse_area_weights.csv").open(newline="", encoding="utf-8")))
    soil_weights = list(csv.DictReader((out_dir / "soil_area_weights.csv").open(newline="", encoding="utf-8")))

    assert summary["ok"] is True
    assert summary["subcatchment_count"] == 2
    assert params["mapping"] == "merged_area_weighted_swmm_params"
    assert params["area_weighting"]["method"] == "polygon_intersection_area_fraction"

    by_id = {row["id"]: row for row in params["by_subcatchment"]}
    assert by_id["S1"]["subcatchment"]["pct_imperv"] == 55.0
    assert by_id["S1"]["subarea"]["n_perv"] == 0.275
    assert by_id["S1"]["infiltration"]["suction_mm"] == 100.0
    assert by_id["S1"]["infiltration"]["ksat_mm_per_hr"] == 9.0
    assert by_id["S2"]["subcatchment"]["pct_imperv"] == 5.0
    assert by_id["S2"]["infiltration"]["ksat_mm_per_hr"] == 2.5
    assert len([row for row in land_weights if row["subcatchment_id"] == "S1"]) == 2
    assert len([row for row in soil_weights if row["subcatchment_id"] == "S1"]) == 2


# ---------------------------------------------------------------------------
# Sibling-skill decoupling seam (issue #246): --skills-root / AISWMM_SKILLS_ROOT
#
# Both scripts assume swmm-params/swmm-network are checked out at
# <repo>/skills/<other-skill>. --skills-root (with an AISWMM_SKILLS_ROOT env
# var fallback) lets a relocated deployment point elsewhere without changing
# default behavior.
# ---------------------------------------------------------------------------

# A generic stand-in for a sibling-skill script: records its own filename and
# argv, writes that record to whichever --output/--report-json path it was
# given (so on-disk artifacts prove the stub ran, not just stdout), and
# prints the same JSON so run_python() in qgis_prepare_swmm_inputs.py can
# parse it into the export manifest.
_STUB_SCRIPT_BODY = '''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
payload = {"ok": True, "stub": True, "script": Path(__file__).name, "argv": args}

out_path = None
for i, a in enumerate(args):
    if a in ("--output", "--report-json") and i + 1 < len(args):
        out_path = Path(args[i + 1])
        break

if out_path is not None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload), encoding="utf-8")

print(json.dumps(payload))
'''


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_stub_skills_root(base: Path) -> Path:
    """Build a fake skills/ root with stub swmm-params/swmm-network scripts."""
    scripts_by_skill = {
        "swmm-params": ["landuse_to_swmm_params.py", "soil_to_greenampt.py", "merge_swmm_params.py"],
        "swmm-network": ["network_qa.py"],
    }
    for skill, script_names in scripts_by_skill.items():
        scripts_dir = base / skill / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for script_name in script_names:
            (scripts_dir / script_name).write_text(_STUB_SCRIPT_BODY, encoding="utf-8")
    return base


def test_resolve_skills_root_default_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("AISWMM_SKILLS_ROOT", raising=False)
    for path, name in [
        (QGIS_SCRIPT, "qgis_prepare_swmm_inputs_seam_default"),
        (AREA_WEIGHTED_SCRIPT, "area_weighted_swmm_params_seam_default"),
    ]:
        module = _load_module(path, name)
        assert module.SKILLS_ROOT_ENV == "AISWMM_SKILLS_ROOT"
        assert module.resolve_skills_root(None) == REPO_ROOT / "skills"


def test_resolve_skills_root_flag_overrides_default(tmp_path: Path) -> None:
    module = _load_module(QGIS_SCRIPT, "qgis_prepare_swmm_inputs_seam_flag")
    assert module.resolve_skills_root(tmp_path) == tmp_path


def test_resolve_skills_root_env_var_overrides_default(tmp_path: Path, monkeypatch) -> None:
    module = _load_module(QGIS_SCRIPT, "qgis_prepare_swmm_inputs_seam_env")
    monkeypatch.setenv("AISWMM_SKILLS_ROOT", str(tmp_path))
    assert module.resolve_skills_root(None) == tmp_path


def test_resolve_skills_root_flag_beats_env_var(tmp_path: Path, monkeypatch) -> None:
    module = _load_module(QGIS_SCRIPT, "qgis_prepare_swmm_inputs_seam_precedence")
    monkeypatch.setenv("AISWMM_SKILLS_ROOT", str(tmp_path / "from-env"))
    flag_dir = tmp_path / "from-flag"
    assert module.resolve_skills_root(flag_dir) == flag_dir


def test_qgis_export_swmm_intermediates_skills_root_flag_targets_stub_scripts(tmp_path: Path) -> None:
    run_dir = tmp_path / "qgis-demo"
    stub_root = _write_stub_skills_root(tmp_path / "stub-skills")

    proc = subprocess.run(
        [
            sys.executable,
            str(QGIS_SCRIPT),
            "export-swmm-intermediates",
            "--case-id",
            "qgis-demo",
            "--run-dir",
            str(run_dir),
            "--subcatchments-geojson",
            str(QGIS_EXAMPLE),
            "--network-json",
            str(NETWORK_EXAMPLE),
            "--default-rain-gage",
            "RG1",
            "--skills-root",
            str(stub_root),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)
    assert summary["ok"] is True

    stages = summary["stage_summaries"]
    assert stages["landuse"] == {
        "ok": True,
        "stub": True,
        "script": "landuse_to_swmm_params.py",
        "argv": stages["landuse"]["argv"],
    }
    assert stages["soil"]["script"] == "soil_to_greenampt.py"
    assert stages["merged_params"]["script"] == "merge_swmm_params.py"
    assert stages["network"]["qa"]["script"] == "network_qa.py"
    assert all(stage["stub"] is True for stage in (stages["landuse"], stages["soil"], stages["merged_params"], stages["network"]["qa"]))

    merged_on_disk = json.loads((run_dir / "02_params/merged_params.json").read_text(encoding="utf-8"))
    assert merged_on_disk["stub"] is True
    qa_on_disk = json.loads((run_dir / "04_network/network_qa.json").read_text(encoding="utf-8"))
    assert qa_on_disk["stub"] is True


def test_qgis_export_swmm_intermediates_env_var_targets_stub_scripts(tmp_path: Path) -> None:
    run_dir = tmp_path / "qgis-demo-env"
    stub_root = _write_stub_skills_root(tmp_path / "stub-skills-env")

    env = os.environ.copy()
    env["AISWMM_SKILLS_ROOT"] = str(stub_root)

    proc = subprocess.run(
        [
            sys.executable,
            str(QGIS_SCRIPT),
            "export-swmm-intermediates",
            "--case-id",
            "qgis-demo-env",
            "--run-dir",
            str(run_dir),
            "--subcatchments-geojson",
            str(QGIS_EXAMPLE),
            "--network-json",
            str(NETWORK_EXAMPLE),
            "--default-rain-gage",
            "RG1",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)
    assert summary["ok"] is True
    assert summary["stage_summaries"]["landuse"]["stub"] is True
    assert summary["stage_summaries"]["soil"]["stub"] is True
    assert summary["stage_summaries"]["merged_params"]["stub"] is True
    assert summary["stage_summaries"]["network"]["qa"]["stub"] is True


def test_qgis_import_drainage_assets_skills_root_flag_targets_stub_network_qa(tmp_path: Path) -> None:
    stub_root = _write_stub_skills_root(tmp_path / "stub-skills-import")
    out_network_json = tmp_path / "network.json"
    out_qa_json = tmp_path / "network_qa.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(QGIS_SCRIPT),
            "import-drainage-assets",
            "--network-json",
            str(NETWORK_EXAMPLE),
            "--out-network-json",
            str(out_network_json),
            "--out-qa-json",
            str(out_qa_json),
            "--skills-root",
            str(stub_root),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(proc.stdout)
    assert result["qa"]["stub"] is True
    assert result["qa"]["script"] == "network_qa.py"
    # The network JSON copy itself is real (unstubbed); only the sibling
    # network_qa.py invocation is redirected to the stub.
    assert out_network_json.read_text(encoding="utf-8") == NETWORK_EXAMPLE.read_text(encoding="utf-8")
    qa_on_disk = json.loads(out_qa_json.read_text(encoding="utf-8"))
    assert qa_on_disk["stub"] is True


def test_area_weighted_swmm_params_skills_root_flag_overrides_lookup_defaults(tmp_path: Path) -> None:
    # Landuse/soil classes deliberately absent from the real swmm-params
    # lookups, resolvable only via a custom lookup CSV under a temp
    # skills-root, to prove --skills-root actually redirects the default
    # lookup file location rather than being accepted and ignored.
    subcatchments = tmp_path / "subcatchments.geojson"
    landuse = tmp_path / "landuse.geojson"
    soil = tmp_path / "soil.geojson"
    out_dir = tmp_path / "weighted"

    gpd.GeoDataFrame(
        [{"basin_id": "S1", "geometry": box(0, 0, 10, 10)}],
        crs="EPSG:32610",
    ).to_file(subcatchments, driver="GeoJSON")
    gpd.GeoDataFrame(
        [{"CLASS": "CustomZone", "geometry": box(0, 0, 10, 10)}],
        crs="EPSG:32610",
    ).to_file(landuse, driver="GeoJSON")
    gpd.GeoDataFrame(
        [{"TEXTURE": "custom_texture", "geometry": box(0, 0, 10, 10)}],
        crs="EPSG:32610",
    ).to_file(soil, driver="GeoJSON")

    stub_skills_root = tmp_path / "stub-skills-params"
    params_refs = stub_skills_root / "swmm-params" / "references"
    params_refs.mkdir(parents=True, exist_ok=True)
    (params_refs / "landuse_class_to_subcatch_params.csv").write_text(
        "landuse_class,imperv_pct,n_imperv,n_perv,dstore_imperv_in,dstore_perv_in,zero_imperv_pct,route_to,pct_routed,notes\n"
        "CustomZone,42,0.02,0.3,0.05,0.2,10,OUTLET,100,test override\n"
        "DEFAULT,0,0.02,0.3,0.05,0.2,10,OUTLET,100,fallback\n",
        encoding="utf-8",
    )
    (params_refs / "soil_texture_to_greenampt.csv").write_text(
        "texture,suction_mm,ksat_mm_per_hr,imdmax,notes\n"
        "custom_texture,321,7,0.4,test override\n"
        "-,100,5,0.4,fallback\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(AREA_WEIGHTED_SCRIPT),
            "--subcatchments",
            str(subcatchments),
            "--landuse",
            str(landuse),
            "--soil",
            str(soil),
            "--out-dir",
            str(out_dir),
            "--skills-root",
            str(stub_skills_root),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)
    assert summary["ok"] is True

    params = json.loads((out_dir / "weighted_params.json").read_text(encoding="utf-8"))
    assert params["sources"]["landuse_lookup"] == str(params_refs / "landuse_class_to_subcatch_params.csv")
    assert params["sources"]["soil_lookup"] == str(params_refs / "soil_texture_to_greenampt.csv")
    by_id = {row["id"]: row for row in params["by_subcatchment"]}
    assert by_id["S1"]["subcatchment"]["pct_imperv"] == 42.0
    assert by_id["S1"]["infiltration"]["suction_mm"] == 321.0
