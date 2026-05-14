from __future__ import annotations

import csv
import json
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
