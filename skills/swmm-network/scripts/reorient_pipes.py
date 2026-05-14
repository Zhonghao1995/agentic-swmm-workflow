#!/usr/bin/env python3
"""Reorient LineString pipes so geometry direction matches flow direction.

Raw municipal storm shapefiles store pipes as LineStrings whose vertex
order reflects digitisation, not flow. The downstream `city_network_adapter`
treats the first endpoint as `from_node` and the last as `to_node`, so
misoriented pipes produce a network that the QA step flags with
`no_outfall_path` warnings.

Algorithm: a breadth-first walk starting from the outfall vertices.
For each pipe touching the current frontier vertex, ensure its `to_node`
end matches the frontier vertex. If not, reverse the LineString. Then
the other endpoint joins the frontier as a newly-known downstream vertex.

Pipes that have no path to any outfall are left with their original
orientation and reported in `unreached_pipes`.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Iterable


def _round_key(x: float, y: float, precision: int) -> tuple[float, float]:
    return (round(x, precision), round(y, precision))


def _line_endpoints(coords: list[list[float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    if len(coords) < 2:
        raise ValueError("LineString must have at least 2 vertices")
    head = (float(coords[0][0]), float(coords[0][1]))
    tail = (float(coords[-1][0]), float(coords[-1][1]))
    return head, tail


def _key(point: tuple[float, float], precision: int) -> tuple[float, float]:
    return _round_key(point[0], point[1], precision)


def _reverse_line(coords: list[list[float]]) -> list[list[float]]:
    return list(reversed(coords))


def _outfall_points(features: list[dict]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for f in features:
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        c = geom.get("coordinates") or []
        if len(c) < 2:
            continue
        points.append((float(c[0]), float(c[1])))
    return points


def _nearest_pipe_vertex(
    target: tuple[float, float],
    pipe_endpoints: Iterable[tuple[tuple[float, float], tuple[float, float]]],
    precision: int,
) -> tuple[float, float] | None:
    """Pick the pipe vertex closest to an outfall point. Returns the rounded key."""
    best: tuple[float, float] | None = None
    best_sq = float("inf")
    for head, tail in pipe_endpoints:
        for endpoint in (head, tail):
            dx = endpoint[0] - target[0]
            dy = endpoint[1] - target[1]
            sq = dx * dx + dy * dy
            if sq < best_sq:
                best_sq = sq
                best = endpoint
    if best is None:
        return None
    return _key(best, precision)


def reorient(
    pipes_geojson: dict,
    outfalls_geojson: dict,
    precision: int = 3,
) -> tuple[dict, dict]:
    features = list(pipes_geojson.get("features") or [])
    outfalls = _outfall_points(list(outfalls_geojson.get("features") or []))
    if not features:
        raise ValueError("pipes geojson has no features")
    if not outfalls:
        raise ValueError("outfalls geojson has no Point features")

    # Map vertex key -> list of pipe indices connected at either endpoint
    vertex_to_pipes: dict[tuple[float, float], list[int]] = {}
    pipe_endpoints: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for idx, feat in enumerate(features):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            raise ValueError(f"pipe feature {idx} is not a LineString")
        head, tail = _line_endpoints(geom["coordinates"])
        pipe_endpoints.append((head, tail))
        head_key = _key(head, precision)
        tail_key = _key(tail, precision)
        vertex_to_pipes.setdefault(head_key, []).append(idx)
        vertex_to_pipes.setdefault(tail_key, []).append(idx)

    # Seed BFS frontier with the pipe vertex closest to each outfall
    frontier: deque[tuple[float, float]] = deque()
    seeded: set[tuple[float, float]] = set()
    for ofall in outfalls:
        seed = _nearest_pipe_vertex(ofall, pipe_endpoints, precision)
        if seed is not None and seed not in seeded:
            seeded.add(seed)
            frontier.append(seed)

    if not frontier:
        raise ValueError("could not seed BFS frontier — no pipe vertex near any outfall")

    visited_vertices: set[tuple[float, float]] = set(seeded)
    visited_pipes: set[int] = set()
    reversed_indices: list[int] = []

    while frontier:
        vertex = frontier.popleft()
        for pipe_idx in vertex_to_pipes.get(vertex, []):
            if pipe_idx in visited_pipes:
                continue
            visited_pipes.add(pipe_idx)
            head, tail = pipe_endpoints[pipe_idx]
            head_key = _key(head, precision)
            tail_key = _key(tail, precision)
            # The pipe must flow INTO the current vertex (to_node == vertex).
            if tail_key == vertex:
                upstream = head_key
            elif head_key == vertex:
                # Need to flip so that the line ends at the current vertex.
                coords = features[pipe_idx]["geometry"]["coordinates"]
                features[pipe_idx]["geometry"]["coordinates"] = _reverse_line(coords)
                pipe_endpoints[pipe_idx] = (tail, head)
                reversed_indices.append(pipe_idx)
                upstream = tail_key
            else:
                # Pipe touches neither endpoint of the visited vertex; defensive skip.
                continue
            if upstream not in visited_vertices:
                visited_vertices.add(upstream)
                frontier.append(upstream)

    unreached = [
        {
            "index": idx,
            "id": features[idx].get("properties", {}).get("FACILITYID")
            or features[idx].get("properties", {}).get("id")
            or f"pipe_{idx}",
        }
        for idx in range(len(features))
        if idx not in visited_pipes
    ]

    out = {
        "type": "FeatureCollection",
        "name": pipes_geojson.get("name", "pipes_oriented"),
        "features": features,
    }
    if "crs" in pipes_geojson:
        out["crs"] = pipes_geojson["crs"]

    report = {
        "ok": True,
        "skill": "swmm-network",
        "tool": "reorient_pipes",
        "counts": {
            "pipes_total": len(features),
            "pipes_reversed": len(reversed_indices),
            "pipes_reached": len(visited_pipes),
            "pipes_unreached": len(unreached),
            "outfalls_used": len(outfalls),
        },
        "reversed_pipe_indices": reversed_indices,
        "unreached_pipes": unreached,
        "coordinate_precision": precision,
    }
    return out, report


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipes-geojson", required=True)
    ap.add_argument("--outfalls-geojson", required=True)
    ap.add_argument("--out", required=True, help="Output path for reoriented pipes geojson.")
    ap.add_argument(
        "--coordinate-precision",
        type=int,
        default=3,
        help="Decimal places used to match pipe endpoints to vertices.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    pipes_path = Path(args.pipes_geojson)
    outfalls_path = Path(args.outfalls_geojson)
    out_path = Path(args.out)
    for p in (pipes_path, outfalls_path):
        if not p.exists():
            raise FileNotFoundError(p)

    pipes = json.loads(pipes_path.read_text(encoding="utf-8"))
    outfalls = json.loads(outfalls_path.read_text(encoding="utf-8"))

    oriented, report = reorient(pipes, outfalls, precision=args.coordinate_precision)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(oriented, indent=2), encoding="utf-8")
    report["outputs"] = {"pipes_oriented_geojson": str(out_path)}
    report["inputs"] = {
        "pipes_geojson": str(pipes_path),
        "outfalls_geojson": str(outfalls_path),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"reorient_pipes failed: {exc}", file=sys.stderr)
        raise
