#!/usr/bin/env python3
"""Pick a SWMM outfall point from raw pipe and watercourse layers.

Two pluggable modes:

- ``endpoint_nearest_watercourse`` (default): scan every pipe endpoint,
  measure its distance to the nearest watercourse geometry, and pick the
  endpoint with the smallest distance. Best when a digitised watercourse
  layer covers the basin's receiving water.

- ``lowest_endpoint``: pick the pipe endpoint with the smallest y
  coordinate. Useful as a fallback when no watercourse layer is
  available; assumes a projected, north-positive CRS so that the
  topographic low is south.

The tool always emits a single outfall (``node_id = OUT1``). Multi-outfall
networks need a follow-up tool; this one is intentionally minimal.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import Point, shape


MODES = ("endpoint_nearest_watercourse", "lowest_endpoint")


def _collect_endpoints(pipes_geojson: dict) -> list[dict]:
    """Return list of {pipe_index, pipe_id, position, point} entries."""
    out: list[dict] = []
    for idx, feat in enumerate(pipes_geojson.get("features") or []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        pid = (
            (feat.get("properties") or {}).get("FACILITYID")
            or (feat.get("properties") or {}).get("id")
            or f"pipe_{idx}"
        )
        out.append({
            "pipe_index": idx,
            "pipe_id": pid,
            "position": "start",
            "point": Point(float(coords[0][0]), float(coords[0][1])),
        })
        out.append({
            "pipe_index": idx,
            "pipe_id": pid,
            "position": "end",
            "point": Point(float(coords[-1][0]), float(coords[-1][1])),
        })
    if not out:
        raise ValueError("no LineString features with endpoints in pipes geojson")
    return out


def _watercourse_geoms(watercourse_geojson: dict) -> list:
    geoms = []
    for feat in watercourse_geojson.get("features") or []:
        geom = feat.get("geometry")
        if geom is None:
            continue
        try:
            geoms.append(shape(geom))
        except Exception:
            continue
    if not geoms:
        raise ValueError("watercourse geojson has no usable geometries")
    return geoms


def _pick_endpoint_nearest_watercourse(endpoints: list[dict], watercourse: list) -> dict:
    best = None
    best_dist = float("inf")
    for ep in endpoints:
        dist = min(ep["point"].distance(g) for g in watercourse)
        if dist < best_dist:
            best_dist = dist
            best = {**ep, "distance_to_watercourse_m": float(dist)}
    if best is None:
        raise RuntimeError("no endpoint chosen — pipes empty?")
    return best


def _pick_lowest_endpoint(endpoints: list[dict]) -> dict:
    best = min(endpoints, key=lambda ep: ep["point"].y)
    return {**best, "distance_to_watercourse_m": None}


def build_outfalls_geojson(chosen: dict, source_crs: dict | None) -> dict:
    pt = chosen["point"]
    properties = {
        "node_id": "OUT1",
        "type": "FREE",
        "invert_elev": 0.0,
        "asset_type": "storm",
        "system_layer": "minor_pipe",
        "source_pipe": chosen["pipe_id"],
        "source_position": chosen["position"],
    }
    if chosen.get("distance_to_watercourse_m") is not None:
        properties["dist_to_watercourse_m"] = chosen["distance_to_watercourse_m"]
    out = {
        "type": "FeatureCollection",
        "name": "outfalls",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Point", "coordinates": [pt.x, pt.y]},
            }
        ],
    }
    if source_crs is not None:
        out["crs"] = source_crs
    return out


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipes-geojson", required=True)
    ap.add_argument(
        "--watercourse-geojson",
        default=None,
        help="Required for mode=endpoint_nearest_watercourse; ignored for mode=lowest_endpoint.",
    )
    ap.add_argument("--mode", choices=MODES, default="endpoint_nearest_watercourse")
    ap.add_argument("--out", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    pipes_path = Path(args.pipes_geojson)
    out_path = Path(args.out)
    if not pipes_path.exists():
        raise FileNotFoundError(pipes_path)

    pipes_geojson = json.loads(pipes_path.read_text(encoding="utf-8"))
    endpoints = _collect_endpoints(pipes_geojson)

    if args.mode == "endpoint_nearest_watercourse":
        if not args.watercourse_geojson:
            raise ValueError("--watercourse-geojson required for mode=endpoint_nearest_watercourse")
        wc_path = Path(args.watercourse_geojson)
        if not wc_path.exists():
            raise FileNotFoundError(wc_path)
        wc_geojson = json.loads(wc_path.read_text(encoding="utf-8"))
        watercourse = _watercourse_geoms(wc_geojson)
        chosen = _pick_endpoint_nearest_watercourse(endpoints, watercourse)
    elif args.mode == "lowest_endpoint":
        chosen = _pick_lowest_endpoint(endpoints)
    else:
        raise ValueError(f"unknown mode: {args.mode}")

    source_crs = pipes_geojson.get("crs")
    outfalls = build_outfalls_geojson(chosen, source_crs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(outfalls, indent=2), encoding="utf-8")

    report = {
        "ok": True,
        "skill": "swmm-network",
        "tool": "infer_outfall",
        "mode": args.mode,
        "chosen": {
            "pipe_id": chosen["pipe_id"],
            "position": chosen["position"],
            "x": chosen["point"].x,
            "y": chosen["point"].y,
            "distance_to_watercourse_m": chosen.get("distance_to_watercourse_m"),
        },
        "counts": {
            "endpoints_considered": len(endpoints),
            "outfalls_emitted": 1,
        },
        "outputs": {"outfalls_geojson": str(out_path)},
        "inputs": {
            "pipes_geojson": str(pipes_path),
            "watercourse_geojson": str(args.watercourse_geojson) if args.watercourse_geojson else None,
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"infer_outfall failed: {exc}", file=sys.stderr)
        raise
