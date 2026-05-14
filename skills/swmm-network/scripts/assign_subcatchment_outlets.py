#!/usr/bin/env python3
"""Assign each SWMM subcatchment to a real network node as its outlet.

Without this step the subcatchments.csv produced by
``basin_shp_to_subcatchments`` carries the literal outfall as every
subcatchment's outlet, which means surface runoff dumps straight to the
outfall and the pipe network sits idle in the SWMM model. This tool
rewrites the ``outlet`` column so runoff actually enters the pipe
network at a sensible upstream node.

Three modes:

- ``nearest_junction`` (default): centroid of each subcatchment polygon
  is matched to the closest node listed in network.json (junctions
  + outfalls). Use when no manhole layer is available.
- ``nearest_catch_basin``: same, but the candidate node set is read
  from a separate manholes / catch-basin GeoJSON. Use when a richer
  catch-basin layer is available than what was inferred during
  network import.
- ``manual_lookup``: read a CSV with two columns
  ``subcatchment_id,outlet_node_id`` and apply the mapping verbatim.
  Use when the agent (or a human) wants to override.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, shape


MODES = ("nearest_junction", "nearest_catch_basin", "manual_lookup")


def _load_subcatchment_centroids(geojson_path: Path) -> list[tuple[str, Point]]:
    obj = json.loads(geojson_path.read_text(encoding="utf-8"))
    out: list[tuple[str, Point]] = []
    for feat in obj.get("features") or []:
        props = feat.get("properties") or {}
        sid = props.get("subcatchment_id")
        if sid is None:
            continue
        geom = shape(feat["geometry"])
        out.append((str(sid), geom.centroid))
    if not out:
        raise ValueError(f"no subcatchments with subcatchment_id field in {geojson_path}")
    return out


def _node_xy(node: dict) -> tuple[float, float] | None:
    """Read xy from either {coordinates: {x, y}} or top-level {x, y}."""
    coords = node.get("coordinates")
    if isinstance(coords, dict) and coords.get("x") is not None and coords.get("y") is not None:
        return float(coords["x"]), float(coords["y"])
    if node.get("x") is not None and node.get("y") is not None:
        return float(node["x"]), float(node["y"])
    return None


def _candidate_nodes_from_network(network_json_path: Path, include_outfalls: bool) -> list[tuple[str, Point]]:
    obj = json.loads(network_json_path.read_text(encoding="utf-8"))
    out: list[tuple[str, Point]] = []
    for j in obj.get("junctions") or []:
        nid = j.get("id")
        xy = _node_xy(j)
        if nid is None or xy is None:
            continue
        out.append((str(nid), Point(*xy)))
    if include_outfalls:
        for o in obj.get("outfalls") or []:
            nid = o.get("id")
            xy = _node_xy(o)
            if nid is None or xy is None:
                continue
            out.append((str(nid), Point(*xy)))
    if not out:
        raise ValueError(f"no candidate nodes in {network_json_path} (junctions+outfalls)")
    return out


def _candidate_nodes_from_geojson(path: Path, id_field: str) -> list[tuple[str, Point]]:
    gdf = gpd.read_file(path)
    if id_field not in gdf.columns:
        raise ValueError(f"id_field '{id_field}' not in {path} columns: {list(gdf.columns)}")
    out: list[tuple[str, Point]] = []
    for _, row in gdf.iterrows():
        nid = row[id_field]
        if nid is None:
            continue
        geom = row.geometry
        if geom is None:
            continue
        if geom.geom_type != "Point":
            geom = geom.centroid
        out.append((str(nid), geom))
    if not out:
        raise ValueError(f"no usable point candidates in {path}")
    return out


def _read_manual_lookup(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "subcatchment_id" not in reader.fieldnames or "outlet_node_id" not in reader.fieldnames:
            raise ValueError(
                f"{path} must have headers 'subcatchment_id' and 'outlet_node_id'; "
                f"got {reader.fieldnames}"
            )
        for row in reader:
            mapping[str(row["subcatchment_id"]).strip()] = str(row["outlet_node_id"]).strip()
    if not mapping:
        raise ValueError(f"{path} has no rows")
    return mapping


def _nearest(point: Point, candidates: list[tuple[str, Point]]) -> tuple[str, float]:
    best_id = None
    best_dist = float("inf")
    for nid, pt in candidates:
        d = point.distance(pt)
        if d < best_dist:
            best_dist = d
            best_id = nid
    if best_id is None:
        raise RuntimeError("no candidates supplied")
    return best_id, float(best_dist)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subcatchments-csv-in", required=True)
    ap.add_argument("--subcatchments-geojson", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--mode", choices=MODES, default="nearest_junction")
    ap.add_argument("--network-json", default=None,
                    help="Required for mode=nearest_junction (provides node coordinates).")
    ap.add_argument("--include-outfalls-as-candidates", action="store_true",
                    help="When mode=nearest_junction, include outfall nodes alongside junctions.")
    ap.add_argument("--candidates-geojson", default=None,
                    help="Required for mode=nearest_catch_basin (Point or polygon layer).")
    ap.add_argument("--candidates-id-field", default="node_id",
                    help="Field name in --candidates-geojson that holds the node id.")
    ap.add_argument("--lookup-csv", default=None,
                    help="Required for mode=manual_lookup; columns: subcatchment_id,outlet_node_id.")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    csv_in = Path(args.subcatchments_csv_in)
    geojson_in = Path(args.subcatchments_geojson)
    csv_out = Path(args.out_csv)
    for p in (csv_in, geojson_in):
        if not p.exists():
            raise FileNotFoundError(p)

    centroids = _load_subcatchment_centroids(geojson_in)

    assignments: dict[str, str] = {}
    distances: dict[str, float] = {}

    if args.mode == "nearest_junction":
        if not args.network_json:
            raise ValueError("mode=nearest_junction requires --network-json")
        candidates = _candidate_nodes_from_network(Path(args.network_json), include_outfalls=args.include_outfalls_as_candidates)
        for sid, c in centroids:
            nid, d = _nearest(c, candidates)
            assignments[sid] = nid
            distances[sid] = d
    elif args.mode == "nearest_catch_basin":
        if not args.candidates_geojson:
            raise ValueError("mode=nearest_catch_basin requires --candidates-geojson")
        candidates = _candidate_nodes_from_geojson(Path(args.candidates_geojson), args.candidates_id_field)
        for sid, c in centroids:
            nid, d = _nearest(c, candidates)
            assignments[sid] = nid
            distances[sid] = d
    elif args.mode == "manual_lookup":
        if not args.lookup_csv:
            raise ValueError("mode=manual_lookup requires --lookup-csv")
        lookup = _read_manual_lookup(Path(args.lookup_csv))
        for sid, _ in centroids:
            if sid not in lookup:
                raise ValueError(f"subcatchment {sid} missing from lookup CSV")
            assignments[sid] = lookup[sid]
    else:
        raise ValueError(f"unknown mode: {args.mode}")

    # Rewrite the CSV with the new outlet column.
    rows_in: list[dict[str, str]] = []
    with csv_in.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "subcatchment_id" not in reader.fieldnames or "outlet" not in reader.fieldnames:
            raise ValueError(
                f"{csv_in} must have 'subcatchment_id' and 'outlet' columns; got {reader.fieldnames}"
            )
        fieldnames = list(reader.fieldnames)
        for row in reader:
            sid = row["subcatchment_id"]
            if sid in assignments:
                row["outlet"] = assignments[sid]
            rows_in.append(row)

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_in)

    report = {
        "ok": True,
        "skill": "swmm-network",
        "tool": "assign_subcatchment_outlets",
        "mode": args.mode,
        "counts": {
            "subcatchments_assigned": len(assignments),
            "subcatchments_in_csv": len(rows_in),
        },
        "assignments": [
            {
                "subcatchment_id": sid,
                "outlet_node_id": nid,
                "centroid_to_node_distance_m": distances.get(sid),
            }
            for sid, nid in assignments.items()
        ],
        "outputs": {"subcatchments_csv": str(csv_out)},
        "inputs": {
            "subcatchments_csv_in": str(csv_in),
            "subcatchments_geojson": str(geojson_in),
            "network_json": str(args.network_json) if args.network_json else None,
            "candidates_geojson": str(args.candidates_geojson) if args.candidates_geojson else None,
            "lookup_csv": str(args.lookup_csv) if args.lookup_csv else None,
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"assign_subcatchment_outlets failed: {exc}", file=sys.stderr)
        raise
