#!/usr/bin/env python3
"""Snap nearby pipe LineString endpoints together so the network is graph-connected.

Real municipal storm pipe shapefiles are usually hand-digitised. Adjacent
pipe segments that should share a manhole often have endpoints separated
by sub-millimetre to several-centimetre vertex drift. The
``city_network_adapter`` infers junctions by exact-coordinate equality
and so treats those drifting endpoints as separate nodes — leaving the
pipe network as a forest of disconnected fragments.

This tool clusters every pipe endpoint within a tolerance and snaps each
cluster to the cluster centroid, rewriting the LineStrings so that
shared endpoints land on identical coordinates.

Algorithm: union-find over endpoints. Two endpoints are merged when
their Euclidean distance is below ``tolerance_m``. Each connected
component's centroid becomes the snapped coordinate. The interior
vertices of each LineString are left untouched; only the first and last
vertices are rewritten.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _euclid(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[ri] = rj


def snap(pipes_geojson: dict, tolerance_m: float) -> tuple[dict, dict]:
    feats = list(pipes_geojson.get("features") or [])
    if not feats:
        raise ValueError("pipes geojson has no features")
    if tolerance_m < 0:
        raise ValueError("tolerance_m must be non-negative")

    # Collect every (feature_index, endpoint_position, point) record.
    endpoints: list[tuple[int, str, tuple[float, float]]] = []
    for fi, f in enumerate(feats):
        geom = f.get("geometry") or {}
        if geom.get("type") != "LineString":
            raise ValueError(f"feature {fi} is not a LineString")
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            raise ValueError(f"feature {fi} has < 2 vertices")
        endpoints.append((fi, "start", (float(coords[0][0]), float(coords[0][1]))))
        endpoints.append((fi, "end", (float(coords[-1][0]), float(coords[-1][1]))))

    n = len(endpoints)
    uf = _UnionFind(n)

    # Bucket endpoints into a grid of size tolerance_m for O(n) clustering.
    # When tolerance_m == 0 there is nothing to snap; just return the input
    # untouched.
    if tolerance_m == 0:
        report = {
            "ok": True,
            "skill": "swmm-network",
            "tool": "snap_pipe_endpoints",
            "tolerance_m": 0.0,
            "counts": {
                "endpoints_total": n,
                "clusters": n,
                "clusters_merged": 0,
                "max_snap_distance_m": 0.0,
            },
        }
        return pipes_geojson, report

    cell = float(tolerance_m)
    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_, _, pt) in enumerate(endpoints):
        key = (int(math.floor(pt[0] / cell)), int(math.floor(pt[1] / cell)))
        buckets.setdefault(key, []).append(idx)

    # For each endpoint, scan the 3x3 neighborhood of buckets for partners.
    for idx, (_, _, pt) in enumerate(endpoints):
        kx = int(math.floor(pt[0] / cell))
        ky = int(math.floor(pt[1] / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for jdx in buckets.get((kx + dx, ky + dy), []):
                    if jdx <= idx:
                        continue
                    if _euclid(pt, endpoints[jdx][2]) <= tolerance_m:
                        uf.union(idx, jdx)

    # Collect cluster centroids and per-cluster max distance.
    clusters: dict[int, list[int]] = {}
    for idx in range(n):
        clusters.setdefault(uf.find(idx), []).append(idx)

    snapped_xy: dict[int, tuple[float, float]] = {}
    max_dist = 0.0
    clusters_merged = 0
    for _, members in clusters.items():
        if len(members) < 2:
            snapped_xy[members[0]] = endpoints[members[0]][2]
            continue
        clusters_merged += 1
        xs = [endpoints[m][2][0] for m in members]
        ys = [endpoints[m][2][1] for m in members]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        for m in members:
            d = _euclid(endpoints[m][2], (cx, cy))
            if d > max_dist:
                max_dist = d
            snapped_xy[m] = (cx, cy)

    # Rewrite the geojson features in place (deep copy not required —
    # caller doesn't reuse pipes_geojson).
    for idx, (fi, pos, _) in enumerate(endpoints):
        new_xy = snapped_xy[idx]
        coords = feats[fi]["geometry"]["coordinates"]
        if pos == "start":
            coords[0] = [new_xy[0], new_xy[1]]
        else:
            coords[-1] = [new_xy[0], new_xy[1]]

    # Drop pipes whose two endpoints landed in the same cluster — they
    # would be self-loop conduits that SWMM and import_city_network
    # both reject. Common causes: an already-tiny pipe in the source
    # data, or a snap tolerance large enough to collapse a short
    # pipe's two ends.
    kept: list[dict] = []
    dropped_self_loops: list[dict] = []
    for fi, f in enumerate(feats):
        coords = f["geometry"]["coordinates"]
        head = (coords[0][0], coords[0][1])
        tail = (coords[-1][0], coords[-1][1])
        if head == tail:
            dropped_self_loops.append({
                "index": fi,
                "id": (f.get("properties") or {}).get("FACILITYID")
                    or (f.get("properties") or {}).get("id")
                    or f"pipe_{fi}",
                "snapped_endpoint": [head[0], head[1]],
            })
        else:
            kept.append(f)

    out = {
        "type": "FeatureCollection",
        "name": pipes_geojson.get("name", "pipes_snapped"),
        "features": kept,
    }
    if "crs" in pipes_geojson:
        out["crs"] = pipes_geojson["crs"]

    report = {
        "ok": True,
        "skill": "swmm-network",
        "tool": "snap_pipe_endpoints",
        "tolerance_m": float(tolerance_m),
        "counts": {
            "endpoints_total": n,
            "clusters": len(clusters),
            "clusters_merged": clusters_merged,
            "max_snap_distance_m": float(max_dist),
            "pipes_in": len(feats),
            "pipes_out": len(kept),
            "pipes_dropped_as_self_loops": len(dropped_self_loops),
        },
        "dropped_self_loops": dropped_self_loops,
    }
    return out, report


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipes-geojson", required=True)
    ap.add_argument("--tolerance-m", type=float, required=True,
                    help="Maximum distance (in CRS units, expected metres) to merge two endpoints.")
    ap.add_argument("--out", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    pipes_path = Path(args.pipes_geojson)
    out_path = Path(args.out)
    if not pipes_path.exists():
        raise FileNotFoundError(pipes_path)

    pipes = json.loads(pipes_path.read_text(encoding="utf-8"))
    snapped, report = snap(pipes, args.tolerance_m)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapped, indent=2), encoding="utf-8")
    report["outputs"] = {"pipes_snapped_geojson": str(out_path)}
    report["inputs"] = {"pipes_geojson": str(pipes_path)}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"snap_pipe_endpoints failed: {exc}", file=sys.stderr)
        raise
