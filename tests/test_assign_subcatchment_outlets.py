from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"


def _seed_subcatchments_csv(path: Path, ids: list[str], default_outlet: str) -> None:
    path.write_text(
        "subcatchment_id,outlet,area_ha,width_m,slope_pct,rain_gage\n"
        + "\n".join(f"{sid},{default_outlet},1.0,100.0,1.0,RG1" for sid in ids)
        + "\n",
        encoding="utf-8",
    )


def _seed_subcatchments_geojson(path: Path, polys: list[tuple[str, list[tuple[float, float]]]]) -> None:
    feats = []
    for sid, ring in polys:
        feats.append({
            "type": "Feature",
            "properties": {"subcatchment_id": sid},
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring + [ring[0]]]]},
        })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")


def _seed_network_json(path: Path, junctions: list[tuple[str, float, float]],
                       outfalls: list[tuple[str, float, float]] | None = None) -> None:
    obj = {
        "junctions": [{"id": jid, "x": x, "y": y} for jid, x, y in junctions],
        "outfalls": [{"id": oid, "x": x, "y": y} for oid, x, y in (outfalls or [])],
        "conduits": [],
    }
    path.write_text(json.dumps(obj), encoding="utf-8")


def _call(tmp_path: Path, args_extra: dict, ids: list[str] = None,
          polys: list = None, outlet_default: str = "OUT1") -> dict:
    ids = ids or ["S1", "S2"]
    if polys is None:
        polys = [
            ("S1", [(0, 0), (10, 0), (10, 10), (0, 10)]),       # centroid (5,5)
            ("S2", [(100, 100), (110, 100), (110, 110), (100, 110)]),  # centroid (105,105)
        ]
    csv_in = tmp_path / "subcatchments.csv"
    geojson_in = tmp_path / "subcatchments.geojson"
    csv_out = tmp_path / "subcatchments_out.csv"
    response = tmp_path / "response.json"
    _seed_subcatchments_csv(csv_in, ids, outlet_default)
    _seed_subcatchments_geojson(geojson_in, polys)
    args_dict = {
        "subcatchmentsCsvIn": str(csv_in),
        "subcatchmentsGeojson": str(geojson_in),
        "outCsv": str(csv_out),
    }
    args_dict.update(args_extra)
    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-network",
            "--tool", "assign_subcatchment_outlets",
            "--arguments-json", json.dumps(args_dict),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    payload_outer = json.loads(response.read_text(encoding="utf-8"))
    report = json.loads(payload_outer["result"]["content"][0]["text"])
    rewritten = csv_out.read_text(encoding="utf-8").splitlines()
    return {"report": report, "csv": rewritten}


def test_nearest_junction_assigns_per_subcatchment(tmp_path: Path) -> None:
    network_path = tmp_path / "network.json"
    _seed_network_json(network_path, junctions=[
        ("J_NEAR_S1", 5.0, 5.0),     # at S1 centroid
        ("J_NEAR_S2", 105.0, 105.0), # at S2 centroid
        ("J_FAR",     500.0, 500.0),
    ])
    result = _call(tmp_path, {"mode": "nearest_junction", "networkJsonPath": str(network_path)})
    rewritten = result["csv"]
    header = rewritten[0].split(",")
    rows = [dict(zip(header, line.split(","))) for line in rewritten[1:]]
    by_id = {r["subcatchment_id"]: r for r in rows}
    assert by_id["S1"]["outlet"] == "J_NEAR_S1"
    assert by_id["S2"]["outlet"] == "J_NEAR_S2"
    assert all(a["outlet_node_id"] for a in result["report"]["assignments"])


def test_nearest_junction_excludes_outfalls_by_default(tmp_path: Path) -> None:
    network_path = tmp_path / "network.json"
    _seed_network_json(network_path,
        junctions=[("J_REAL", 1000.0, 1000.0)],
        outfalls=[("OUT1", 5.0, 5.0)])  # outfall is closest
    result = _call(tmp_path, {"mode": "nearest_junction", "networkJsonPath": str(network_path)},
                   ids=["S1"], polys=[("S1", [(0, 0), (10, 0), (10, 10), (0, 10)])])
    by_id = {r.split(",")[0]: r for r in result["csv"][1:]}
    # By default outfalls are excluded; J_REAL wins despite being far.
    assert by_id["S1"].split(",")[1] == "J_REAL"


def test_manual_lookup_overrides_csv(tmp_path: Path) -> None:
    lookup = tmp_path / "lookup.csv"
    lookup.write_text("subcatchment_id,outlet_node_id\nS1,J_X\nS2,J_Y\n", encoding="utf-8")
    result = _call(tmp_path, {"mode": "manual_lookup", "lookupCsvPath": str(lookup)})
    by_id = {r.split(",")[0]: r.split(",")[1] for r in result["csv"][1:]}
    assert by_id == {"S1": "J_X", "S2": "J_Y"}
