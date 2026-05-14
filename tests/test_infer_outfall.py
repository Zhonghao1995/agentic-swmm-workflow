from __future__ import annotations

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


def _call(tmp_path: Path, mode: str | None, with_watercourse: bool, pipes_features: list[dict],
          watercourse_features: list[dict] | None = None) -> dict:
    pipes_path = tmp_path / "pipes.geojson"
    out_path = tmp_path / "outfalls.geojson"
    response = tmp_path / "response.json"
    _write_geojson(pipes_path, pipes_features)
    args_dict: dict = {"pipesGeojsonPath": str(pipes_path), "outPath": str(out_path)}
    if mode:
        args_dict["mode"] = mode
    if with_watercourse:
        wc_path = tmp_path / "watercourse.geojson"
        _write_geojson(wc_path, watercourse_features or [])
        args_dict["watercourseGeojsonPath"] = str(wc_path)
    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "infer_outfall",
            "--arguments-json", json.dumps(args_dict),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    report = json.loads(payload_outer["result"]["content"][0]["text"])
    out = json.loads(out_path.read_text(encoding="utf-8"))
    return {"report": report, "outfalls": out}


def test_infer_outfall_endpoint_nearest_watercourse(tmp_path: Path) -> None:
    # P1 (0,0)-(10,0). Watercourse at (12,0). The (10,0) endpoint is closer.
    pipes = [_pipe(1, (0.0, 0.0), (10.0, 0.0))]
    watercourse = [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "LineString", "coordinates": [[12.0, -5.0], [12.0, 5.0]]},
    }]
    result = _call(tmp_path, mode="endpoint_nearest_watercourse", with_watercourse=True,
                   pipes_features=pipes, watercourse_features=watercourse)
    assert result["report"]["mode"] == "endpoint_nearest_watercourse"
    chosen = result["report"]["chosen"]
    assert chosen["x"] == 10.0 and chosen["y"] == 0.0
    assert chosen["pipe_id"] == "P1"
    assert chosen["position"] == "end"
    assert chosen["distance_to_watercourse_m"] == 2.0

    feats = result["outfalls"]["features"]
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["node_id"] == "OUT1"
    assert props["type"] == "FREE"
    assert props["source_pipe"] == "P1"
    assert props["dist_to_watercourse_m"] == 2.0


def test_infer_outfall_lowest_endpoint(tmp_path: Path) -> None:
    # 3 pipes, endpoints at varying y; min y is -7 (start of P2).
    pipes = [
        _pipe(1, (0.0, 0.0), (10.0, 0.0)),
        _pipe(2, (5.0, -7.0), (5.0, 3.0)),   # start y=-7
        _pipe(3, (8.0, 2.0), (8.0, 6.0)),
    ]
    result = _call(tmp_path, mode="lowest_endpoint", with_watercourse=False, pipes_features=pipes)
    chosen = result["report"]["chosen"]
    assert chosen["x"] == 5.0 and chosen["y"] == -7.0
    assert chosen["pipe_id"] == "P2"
    assert chosen["position"] == "start"
    assert chosen["distance_to_watercourse_m"] is None
    props = result["outfalls"]["features"][0]["properties"]
    assert props["node_id"] == "OUT1"
    assert "dist_to_watercourse_m" not in props
