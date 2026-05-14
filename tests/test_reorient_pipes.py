import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"


def _write_geojson(path: Path, features: list[dict], crs_epsg: int = 32610) -> None:
    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{crs_epsg}"}},
        "features": features,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pipe(idx: int, head: tuple[float, float], tail: tuple[float, float]) -> dict:
    return {
        "type": "Feature",
        "properties": {"FACILITYID": f"P{idx}"},
        "geometry": {"type": "LineString", "coordinates": [list(head), list(tail)]},
    }


def _outfall(coords: tuple[float, float]) -> dict:
    return {
        "type": "Feature",
        "properties": {"node_id": "OUT1"},
        "geometry": {"type": "Point", "coordinates": list(coords)},
    }


def _run(tmp_path: Path) -> dict:
    pipes_path = tmp_path / "pipes.geojson"
    outfalls_path = tmp_path / "outfalls.geojson"
    out_path = tmp_path / "pipes_oriented.geojson"
    response = tmp_path / "response.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "reorient_pipes",
            "--arguments-json", json.dumps({
                "pipesGeojsonPath": str(pipes_path),
                "outfallsGeojsonPath": str(outfalls_path),
                "outPath": str(out_path),
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
    out_geojson = json.loads(out_path.read_text(encoding="utf-8"))
    return {"summary": json.loads(proc.stdout), "report": payload, "out": out_geojson, "out_path": out_path}


def test_reorient_pipes_flips_misaligned_lines(tmp_path: Path) -> None:
    # Linear chain: P1 (0,0)->(1,0)  P2 (2,0)->(1,0)  outfall at (0,0)
    # P2's geometry direction is (2,0)->(1,0), which is correct since it must
    # flow into (1,0). P1's geometry direction is (0,0)->(1,0), which is
    # WRONG: water should flow from (1,0) toward outfall at (0,0), i.e.
    # (1,0)->(0,0). The tool should flip P1.
    pipes_path = tmp_path / "pipes.geojson"
    outfalls_path = tmp_path / "outfalls.geojson"
    _write_geojson(pipes_path, [
        _pipe(1, (0.0, 0.0), (1.0, 0.0)),   # mis-oriented (away from outfall)
        _pipe(2, (2.0, 0.0), (1.0, 0.0)),   # correctly oriented
    ])
    _write_geojson(outfalls_path, [_outfall((0.0, 0.0))])

    result = _run(tmp_path)
    report = result["report"]
    out = result["out"]

    assert report["counts"]["pipes_total"] == 2
    assert report["counts"]["pipes_reached"] == 2
    assert report["counts"]["pipes_reversed"] == 1
    assert report["counts"]["pipes_unreached"] == 0
    assert 0 in report["reversed_pipe_indices"]
    assert 1 not in report["reversed_pipe_indices"]

    # After reorient: P1 must end at (0,0)
    p1_coords = out["features"][0]["geometry"]["coordinates"]
    assert p1_coords[-1] == [0.0, 0.0]
    # P2 unchanged: ends at (1,0)
    p2_coords = out["features"][1]["geometry"]["coordinates"]
    assert p2_coords[-1] == [1.0, 0.0]


def test_reorient_pipes_reports_unreached_disconnected_pipes(tmp_path: Path) -> None:
    # P1 connects to outfall at (0,0); P2 is an isolated pipe far away.
    pipes_path = tmp_path / "pipes.geojson"
    outfalls_path = tmp_path / "outfalls.geojson"
    _write_geojson(pipes_path, [
        _pipe(1, (1.0, 0.0), (0.0, 0.0)),   # already correct
        _pipe(2, (50.0, 50.0), (60.0, 60.0)),  # disconnected
    ])
    _write_geojson(outfalls_path, [_outfall((0.0, 0.0))])

    result = _run(tmp_path)
    report = result["report"]

    assert report["counts"]["pipes_reached"] == 1
    assert report["counts"]["pipes_unreached"] == 1
    unreached_ids = [u["id"] for u in report["unreached_pipes"]]
    assert "P2" in unreached_ids
