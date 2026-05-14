import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"


def test_framework_harness_calls_gis_area_weighted_params_through_mcp(tmp_path: Path) -> None:
    subcatchments = tmp_path / "subcatchments.geojson"
    landuse = tmp_path / "landuse.geojson"
    soil = tmp_path / "soil.geojson"
    out_dir = tmp_path / "weighted"
    response = tmp_path / "mcp_response.json"

    gpd.GeoDataFrame([{"subcatchment_id": "S1", "geometry": box(0, 0, 10, 10)}], crs="EPSG:32610").to_file(
        subcatchments, driver="GeoJSON"
    )
    gpd.GeoDataFrame([{"CLASS": "Rural", "geometry": box(0, 0, 10, 10)}], crs="EPSG:32610").to_file(
        landuse, driver="GeoJSON"
    )
    gpd.GeoDataFrame([{"TEXTURE": "loam", "geometry": box(0, 0, 10, 10)}], crs="EPSG:32610").to_file(
        soil, driver="GeoJSON"
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--server-dir",
            "mcp/swmm-gis",
            "--tool",
            "qgis_area_weighted_params",
            "--arguments-json",
            json.dumps(
                {
                    "subcatchments": str(subcatchments),
                    "landuse": str(landuse),
                    "soil": str(soil),
                    "outDir": str(out_dir),
                    "idField": "subcatchment_id",
                    "landuseField": "CLASS",
                    "soilField": "TEXTURE",
                }
            ),
            "--out-response",
            str(response),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(proc.stdout)
    payload = json.loads(response.read_text(encoding="utf-8"))
    params = json.loads((out_dir / "weighted_params.json").read_text(encoding="utf-8"))

    assert summary["ok"] is True
    assert summary["transport"] == "mcp_stdio"
    assert summary["tool"] == "qgis_area_weighted_params"
    assert payload["result"]["content"]
    assert params["counts"]["subcatchment_count"] == 1


def test_framework_harness_calls_climate_formatter_through_mcp(tmp_path: Path) -> None:
    rainfall = tmp_path / "rainfall.csv"
    rainfall.write_text(
        "timestamp,rainfall_mm_per_hr\n"
        "1984-04-09 00:00:00,0.25\n"
        "1984-04-10 00:00:00,0.10\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "rainfall.json"
    out_timeseries = tmp_path / "timeseries.txt"
    response = tmp_path / "mcp_response.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--server-dir",
            "mcp/swmm-climate",
            "--tool",
            "format_rainfall",
            "--arguments-json",
            json.dumps(
                {
                    "inputCsvPath": str(rainfall),
                    "outputJsonPath": str(out_json),
                    "outputTimeseriesPath": str(out_timeseries),
                    "seriesName": "TS_TEST",
                    "timestampColumn": "timestamp",
                    "valueColumn": "rainfall_mm_per_hr",
                    "timestampFormat": "%Y-%m-%d %H:%M:%S",
                    "valueUnits": "mm_per_hr",
                    "unitPolicy": "strict",
                    "timestampPolicy": "sort",
                }
            ),
            "--out-response",
            str(response),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(proc.stdout)
    climate = json.loads(out_json.read_text(encoding="utf-8"))
    timeseries = out_timeseries.read_text(encoding="utf-8")

    assert summary["ok"] is True
    assert summary["transport"] == "mcp_stdio"
    assert summary["tool"] == "format_rainfall"
    assert climate["series_name"] == "TS_TEST"
    assert "TS_TEST" in timeseries
