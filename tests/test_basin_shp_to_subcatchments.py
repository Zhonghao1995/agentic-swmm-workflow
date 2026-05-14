from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"


def _seed_basin(tmp_path: Path) -> Path:
    """3 polygons of varying area, with an OBJECTID field."""
    basin_path = tmp_path / "basins.shp"
    features = [
        {"OBJECTID": 1, "geometry": Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])},  # 100 m^2
        {"OBJECTID": 2, "geometry": Polygon([(20, 0), (40, 0), (40, 20), (20, 20)])},  # 400 m^2 (largest)
        {"OBJECTID": 3, "geometry": Polygon([(50, 0), (60, 0), (60, 5), (50, 5)])},  # 50 m^2
    ]
    gpd.GeoDataFrame(features, crs="EPSG:32610").to_file(basin_path)
    return basin_path


def _call_tool(tmp_path: Path, args_extra: dict) -> dict:
    basin = _seed_basin(tmp_path)
    out_geojson = tmp_path / "subcatchments.geojson"
    out_csv = tmp_path / "subcatchments.csv"
    response = tmp_path / "response.json"
    args_dict = {
        "basinShp": str(basin),
        "outGeojson": str(out_geojson),
        "outCsv": str(out_csv),
    }
    args_dict.update(args_extra)
    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-gis",
            "--tool", "basin_shp_to_subcatchments",
            "--arguments-json", json.dumps(args_dict),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    report = json.loads(payload_outer["result"]["content"][0]["text"])
    geojson_obj = json.loads(out_geojson.read_text(encoding="utf-8"))
    csv_rows = out_csv.read_text(encoding="utf-8").splitlines()
    return {"report": report, "geojson": geojson_obj, "csv_rows": csv_rows}


def test_by_id_field_selection(tmp_path: Path) -> None:
    result = _call_tool(tmp_path, {"mode": "by_id_field", "idField": "OBJECTID", "idValue": 2})
    assert result["report"]["counts"]["subcatchments_emitted"] == 1
    # Header + 1 row
    assert len(result["csv_rows"]) == 2
    header = result["csv_rows"][0].split(",")
    assert header == ["subcatchment_id", "outlet", "area_ha", "width_m", "slope_pct", "rain_gage"]
    row = dict(zip(header, result["csv_rows"][1].split(",")))
    # OBJECTID=2 polygon area is 400 m^2 = 0.04 ha
    assert abs(float(row["area_ha"]) - 0.04) < 1e-6
    # width = sqrt(400) = 20
    assert abs(float(row["width_m"]) - 20.0) < 1e-6
    assert row["subcatchment_id"] == "S1"
    assert row["outlet"] == "OUT1"
    assert row["rain_gage"] == "RG1"
    assert abs(float(row["slope_pct"]) - 1.0) < 1e-6


def test_largest_mode_picks_max_area(tmp_path: Path) -> None:
    result = _call_tool(tmp_path, {"mode": "largest"})
    assert result["report"]["counts"]["subcatchments_emitted"] == 1
    row = dict(zip(
        result["csv_rows"][0].split(","),
        result["csv_rows"][1].split(","),
    ))
    # Largest is OBJECTID=2, area=400 m^2 -> 0.04 ha
    assert abs(float(row["area_ha"]) - 0.04) < 1e-6


def test_all_mode_emits_one_subcatchment_per_feature(tmp_path: Path) -> None:
    result = _call_tool(tmp_path, {"mode": "all", "idPrefix": "SUB"})
    assert result["report"]["counts"]["subcatchments_emitted"] == 3
    # 3 rows + header
    assert len(result["csv_rows"]) == 4
    ids = [r.split(",")[0] for r in result["csv_rows"][1:]]
    assert ids == ["SUB1", "SUB2", "SUB3"]


def test_defaults_override(tmp_path: Path) -> None:
    result = _call_tool(tmp_path, {
        "mode": "by_id_field", "idField": "OBJECTID", "idValue": 1,
        "outletNodeId": "MY_OUT", "rainGageId": "RG2", "defaultSlopePct": 2.5,
    })
    row = dict(zip(
        result["csv_rows"][0].split(","),
        result["csv_rows"][1].split(","),
    ))
    assert row["outlet"] == "MY_OUT"
    assert row["rain_gage"] == "RG2"
    assert abs(float(row["slope_pct"]) - 2.5) < 1e-6
