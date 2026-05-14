import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, box


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"
TEMPLATE = REPO_ROOT / "skills/swmm-network/templates/city_mapping_raw_shapefile.template.json"


def _seed_synthetic_shapefiles(tmp_path: Path) -> dict[str, Path]:
    pipes = tmp_path / "pipes.shp"
    manholes = tmp_path / "manholes.shp"
    basin = tmp_path / "basin.geojson"

    pipe_features = [
        # inside the basin
        {"FACILITYID": "P1", "DIAMETER": "0.3", "CRSECSHAPE": "Circular",
         "MATERIAL": "Concrete", "geometry": LineString([(2, 2), (5, 2)])},
        {"FACILITYID": "P2", "DIAMETER": "Other", "CRSECSHAPE": "Circular",
         "MATERIAL": "Concrete", "geometry": LineString([(5, 2), (8, 5)])},
        # entirely outside the basin
        {"FACILITYID": "P3", "DIAMETER": "0.3", "CRSECSHAPE": "Circular",
         "MATERIAL": "PVC", "geometry": LineString([(50, 50), (60, 60)])},
    ]
    gpd.GeoDataFrame(pipe_features, crs="EPSG:32610").to_file(pipes)

    manhole_features = [
        {"node_id": "MH1", "geometry": Point(2, 2)},
        {"node_id": "MH2", "geometry": Point(5, 2)},
        {"node_id": "MH3", "geometry": Point(8, 5)},
        # outside the basin
        {"node_id": "MH4", "geometry": Point(50, 50)},
    ]
    gpd.GeoDataFrame(manhole_features, crs="EPSG:32610").to_file(manholes)

    gpd.GeoDataFrame(
        [{"id": 1, "geometry": box(0, 0, 10, 10)}],
        crs="EPSG:32610",
    ).to_file(basin, driver="GeoJSON")

    return {"pipes": pipes, "manholes": manholes, "basin": basin}


def test_prepare_storm_inputs_clips_layers_and_fills_mapping(tmp_path: Path) -> None:
    paths = _seed_synthetic_shapefiles(tmp_path)
    out_dir = tmp_path / "out"
    response = tmp_path / "response.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "prepare_storm_inputs",
            "--arguments-json", json.dumps({
                "pipesShpPath": str(paths["pipes"]),
                "manholesShpPath": str(paths["manholes"]),
                "basinClipGeojsonPath": str(paths["basin"]),
                "mappingTemplatePath": str(TEMPLATE),
                "outDir": str(out_dir),
                "caseName": "unit-test-clip",
                "sourceDescription": "synthetic pipes + manholes box test",
                "diameterPolicy": "test fallback geom1=0.3",
            }),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(proc.stdout)
    assert summary["ok"] is True
    assert summary["tool"] == "prepare_storm_inputs"

    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    payload_text = payload_outer["result"]["content"][0]["text"]
    payload = json.loads(payload_text)
    assert payload["ok"] is True
    assert payload["counts"]["pipes_clipped"] == 2
    assert payload["counts"]["manholes_clipped"] == 3

    pipes_out = out_dir / "pipes.geojson"
    manholes_out = out_dir / "manholes.geojson"
    mapping_out = out_dir / "mapping.json"
    assert pipes_out.exists() and manholes_out.exists() and mapping_out.exists()

    mapping = json.loads(mapping_out.read_text(encoding="utf-8"))
    assert mapping["meta"]["name"] == "unit-test-clip"
    assert mapping["meta"]["source"] == "synthetic pipes + manholes box test"
    assert mapping["meta"]["diameter_policy"] == "test fallback geom1=0.3"
    # field-mapping defaults preserved from template
    assert mapping["pipes"]["fields"]["id"] == "FACILITYID"
    assert mapping["dual_system_ready"] is False


def test_prepare_storm_inputs_works_without_manholes(tmp_path: Path) -> None:
    paths = _seed_synthetic_shapefiles(tmp_path)
    out_dir = tmp_path / "out"
    response = tmp_path / "response.json"

    subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "prepare_storm_inputs",
            "--arguments-json", json.dumps({
                "pipesShpPath": str(paths["pipes"]),
                "basinClipGeojsonPath": str(paths["basin"]),
                "mappingTemplatePath": str(TEMPLATE),
                "outDir": str(out_dir),
                "caseName": "unit-test-no-manholes",
                "sourceDescription": "pipes-only test",
            }),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    payload = json.loads(payload_outer["result"]["content"][0]["text"])
    assert payload["counts"]["pipes_clipped"] == 2
    assert payload["counts"]["manholes_clipped"] is None
    assert not (out_dir / "manholes.geojson").exists()
