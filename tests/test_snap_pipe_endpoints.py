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


def _call(tmp_path: Path, pipes: list[dict], tolerance: float) -> dict:
    pipes_path = tmp_path / "pipes.geojson"
    out_path = tmp_path / "snapped.geojson"
    response = tmp_path / "response.json"
    _write_geojson(pipes_path, pipes)
    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "snap_pipe_endpoints",
            "--arguments-json", json.dumps({
                "pipesGeojsonPath": str(pipes_path),
                "toleranceM": tolerance,
                "outPath": str(out_path),
            }),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    report = json.loads(payload_outer["result"]["content"][0]["text"])
    out = json.loads(out_path.read_text(encoding="utf-8"))
    return {"report": report, "out": out}


def test_snap_merges_close_endpoints(tmp_path: Path) -> None:
    # Two pipes that should share an endpoint at (10,0), but the second pipe's
    # start is digitised at (10.0005, 0.0003) — sub-millimetre drift.
    pipes = [
        _pipe(1, (0.0, 0.0), (10.0, 0.0)),
        _pipe(2, (10.0005, 0.0003), (20.0, 0.0)),
    ]
    result = _call(tmp_path, pipes, tolerance=0.01)
    rep = result["report"]
    assert rep["counts"]["clusters_merged"] == 1
    # After snap: P1's tail and P2's head must be identical
    p1_tail = result["out"]["features"][0]["geometry"]["coordinates"][-1]
    p2_head = result["out"]["features"][1]["geometry"]["coordinates"][0]
    assert p1_tail == p2_head


def test_snap_leaves_far_apart_endpoints_alone(tmp_path: Path) -> None:
    pipes = [
        _pipe(1, (0.0, 0.0), (10.0, 0.0)),
        _pipe(2, (50.0, 50.0), (60.0, 60.0)),
    ]
    result = _call(tmp_path, pipes, tolerance=0.5)
    rep = result["report"]
    assert rep["counts"]["clusters_merged"] == 0


def test_snap_zero_tolerance_is_noop(tmp_path: Path) -> None:
    # Even endpoints right next to each other are not snapped at tolerance=0.
    pipes = [
        _pipe(1, (0.0, 0.0), (10.0, 0.0)),
        _pipe(2, (10.0001, 0.0), (20.0, 0.0)),
    ]
    result = _call(tmp_path, pipes, tolerance=0.0)
    rep = result["report"]
    assert rep["counts"]["clusters_merged"] == 0
    p1_tail = result["out"]["features"][0]["geometry"]["coordinates"][-1]
    p2_head = result["out"]["features"][1]["geometry"]["coordinates"][0]
    assert p1_tail != p2_head


def test_snap_drops_pipes_collapsed_to_self_loops(tmp_path: Path) -> None:
    # P1 is a tiny pipe: head and tail are within tolerance, so the snap will
    # collapse it into a self-loop. P2 is normal.
    pipes = [
        _pipe(1, (5.0, 5.0), (5.05, 5.05)),       # tiny — both ends would snap to same cluster
        _pipe(2, (50.0, 50.0), (60.0, 60.0)),
    ]
    result = _call(tmp_path, pipes, tolerance=1.0)
    rep = result["report"]
    assert rep["counts"]["pipes_dropped_as_self_loops"] == 1
    assert rep["counts"]["pipes_in"] == 2
    assert rep["counts"]["pipes_out"] == 1
    # Surviving pipe is P2.
    surviving_ids = [f["properties"]["FACILITYID"] for f in result["out"]["features"]]
    assert surviving_ids == ["P2"]
    assert rep["dropped_self_loops"][0]["id"] == "P1"


def test_snap_three_endpoints_into_one_cluster(tmp_path: Path) -> None:
    # Three pipes meeting at the same logical point with drift.
    pipes = [
        _pipe(1, (0.0, 0.0), (10.0, 10.0)),
        _pipe(2, (10.001, 10.001), (20.0, 0.0)),
        _pipe(3, (10.002, 9.999), (5.0, 20.0)),
    ]
    result = _call(tmp_path, pipes, tolerance=0.5)
    rep = result["report"]
    assert rep["counts"]["clusters_merged"] == 1
    # Get all endpoints touching the cluster (after snap they must coincide)
    p1_tail = result["out"]["features"][0]["geometry"]["coordinates"][-1]
    p2_head = result["out"]["features"][1]["geometry"]["coordinates"][0]
    p3_head = result["out"]["features"][2]["geometry"]["coordinates"][0]
    assert p1_tail == p2_head == p3_head
