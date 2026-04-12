#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def parse_xy(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"Invalid coordinate value: {value}")
    return float(value[0]), float(value[1])


def ensure_closed_ring(ring: list[list[float]]) -> list[list[float]]:
    if len(ring) < 3:
        raise ValueError("Polygon ring must have at least 3 points")
    first = ring[0]
    last = ring[-1]
    if float(first[0]) == float(last[0]) and float(first[1]) == float(last[1]):
        return ring
    return ring + [first]


def ring_metrics(ring: list[list[float]]) -> tuple[float, float, float, float]:
    closed = ensure_closed_ring(ring)

    double_area = 0.0
    cx_num = 0.0
    cy_num = 0.0
    perimeter = 0.0

    for i in range(len(closed) - 1):
        x0, y0 = parse_xy(closed[i])
        x1, y1 = parse_xy(closed[i + 1])
        cross = x0 * y1 - x1 * y0
        double_area += cross
        cx_num += (x0 + x1) * cross
        cy_num += (y0 + y1) * cross
        perimeter += math.hypot(x1 - x0, y1 - y0)

    area = 0.5 * double_area
    if abs(area) < 1e-12:
        xs = [parse_xy(pt)[0] for pt in closed[:-1]]
        ys = [parse_xy(pt)[1] for pt in closed[:-1]]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys), perimeter

    cx = cx_num / (6.0 * area)
    cy = cy_num / (6.0 * area)
    return area, cx, cy, perimeter


def polygon_metrics(polygon_coords: list[Any]) -> tuple[float, float, float, float]:
    if not polygon_coords:
        raise ValueError("Polygon has no rings")

    outer = polygon_coords[0]
    a_outer, cx_outer, cy_outer, outer_perimeter = ring_metrics(outer)
    area_outer = abs(a_outer)
    if area_outer < 1e-12:
        raise ValueError("Polygon outer ring has zero area")

    hole_area_sum = 0.0
    hole_cx_sum = 0.0
    hole_cy_sum = 0.0
    for hole in polygon_coords[1:]:
        a_hole, cx_hole, cy_hole, _ = ring_metrics(hole)
        ah = abs(a_hole)
        hole_area_sum += ah
        hole_cx_sum += cx_hole * ah
        hole_cy_sum += cy_hole * ah

    net_area = area_outer - hole_area_sum
    if net_area <= 1e-12:
        raise ValueError("Polygon net area is <= 0 after holes")

    cx = (cx_outer * area_outer - hole_cx_sum) / net_area
    cy = (cy_outer * area_outer - hole_cy_sum) / net_area
    return net_area, cx, cy, outer_perimeter


def geometry_metrics(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon":
        return polygon_metrics(coords)

    if gtype == "MultiPolygon":
        total_area = 0.0
        total_perimeter = 0.0
        cx_weighted = 0.0
        cy_weighted = 0.0
        for poly in coords:
            area, cx, cy, perimeter = polygon_metrics(poly)
            total_area += area
            total_perimeter += perimeter
            cx_weighted += cx * area
            cy_weighted += cy * area
        if total_area <= 1e-12:
            raise ValueError("MultiPolygon total area is <= 0")
        return total_area, cx_weighted / total_area, cy_weighted / total_area, total_perimeter

    raise ValueError(f"Unsupported geometry type: {gtype}")


def parse_optional_float(props: dict[str, Any], field: str) -> float | None:
    raw = props.get(field)
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid float in property '{field}': {raw}") from exc


def build_node_index(network_json: Path) -> dict[str, tuple[float, float]]:
    network = load_json(network_json)
    nodes: dict[str, tuple[float, float]] = {}

    for node in list(network.get("junctions") or []) + list(network.get("outfalls") or []):
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            raise ValueError(f"Node with missing id in {network_json}")
        coords = node.get("coordinates")
        if not isinstance(coords, dict):
            raise ValueError(f"Node '{node_id}' missing coordinates in {network_json}")
        x = float(coords.get("x"))
        y = float(coords.get("y"))
        nodes[node_id] = (x, y)

    if not nodes:
        raise ValueError(f"No nodes found in network JSON: {network_json}")
    return nodes


def nearest_node(
    centroid_x: float,
    centroid_y: float,
    nodes: dict[str, tuple[float, float]],
) -> tuple[str, float]:
    best_id: str | None = None
    best_dist = float("inf")
    for node_id, (nx, ny) in nodes.items():
        d = math.hypot(centroid_x - nx, centroid_y - ny)
        if d < best_dist:
            best_dist = d
            best_id = node_id
    if best_id is None:
        raise ValueError("No nodes available for nearest-node search")
    return best_id, best_dist


def estimate_width_m(area_m2: float, perimeter_m: float, min_width_m: float) -> float:
    # Deterministic surrogate: equivalent hydraulic width from area and perimeter.
    width = 2.0 * area_m2 / max(perimeter_m, 1e-9)
    return max(width, min_width_m)


def estimate_slope_pct(
    props: dict[str, Any],
    flow_length_m: float,
    *,
    default_slope_pct: float,
    min_slope_pct: float,
) -> tuple[float, str]:
    slope_direct = parse_optional_float(props, "slope_pct")
    if slope_direct is not None:
        return max(slope_direct, min_slope_pct), "property:slope_pct"

    elev_mean = parse_optional_float(props, "elev_mean_m")
    elev_outlet = parse_optional_float(props, "elev_outlet_m")
    if elev_mean is not None and elev_outlet is not None:
        slope = (elev_mean - elev_outlet) / max(flow_length_m, 1e-9) * 100.0
        return max(slope, min_slope_pct), "derived:(elev_mean_m-elev_outlet_m)/flow_length"

    return max(default_slope_pct, min_slope_pct), "default"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Preprocess subcatchment polygons into builder-ready CSV with deterministic width/slope/outlet linking."
    )
    ap.add_argument("--subcatchments-geojson", type=Path, required=True)
    ap.add_argument("--network-json", type=Path, required=True)
    ap.add_argument("--out-csv", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--id-field", default="subcatchment_id")
    ap.add_argument("--outlet-hint-field", default="outlet_hint")
    ap.add_argument("--default-slope-pct", type=float, default=1.0)
    ap.add_argument("--min-slope-pct", type=float, default=0.1)
    ap.add_argument("--min-width-m", type=float, default=10.0)
    ap.add_argument("--default-curb-length-m", type=float, default=0.0)
    ap.add_argument("--default-rain-gage", default="")
    ap.add_argument("--max-link-distance-m", type=float, default=None)
    args = ap.parse_args()

    if args.default_slope_pct <= 0:
        raise ValueError("--default-slope-pct must be > 0")
    if args.min_slope_pct <= 0:
        raise ValueError("--min-slope-pct must be > 0")
    if args.min_width_m <= 0:
        raise ValueError("--min-width-m must be > 0")

    gj = load_json(args.subcatchments_geojson)
    if gj.get("type") != "FeatureCollection":
        raise ValueError("subcatchments GeoJSON must be a FeatureCollection")
    features = gj.get("features") or []
    if not isinstance(features, list) or not features:
        raise ValueError("subcatchments GeoJSON has no features")

    node_index = build_node_index(args.network_json)

    seen_ids: set[str] = set()
    csv_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for idx, feature in enumerate(features):
        props = feature.get("properties") or {}
        geom = feature.get("geometry")
        if not isinstance(geom, dict):
            raise ValueError(f"Feature index {idx} has no geometry")

        feature_id = props.get(args.id_field)
        if feature_id is None:
            feature_id = props.get("id")
        if feature_id is None:
            feature_id = feature.get("id")
        subcatchment_id = str(feature_id or "").strip()
        if not subcatchment_id:
            raise ValueError(f"Feature index {idx} missing id field '{args.id_field}'")
        if subcatchment_id in seen_ids:
            raise ValueError(f"Duplicate subcatchment id '{subcatchment_id}'")
        seen_ids.add(subcatchment_id)

        area_m2, cx, cy, perimeter_m = geometry_metrics(geom)
        area_ha = area_m2 / 10000.0
        width_m = estimate_width_m(area_m2, perimeter_m, args.min_width_m)
        flow_length_m = area_m2 / width_m
        slope_pct, slope_source = estimate_slope_pct(
            props,
            flow_length_m,
            default_slope_pct=args.default_slope_pct,
            min_slope_pct=args.min_slope_pct,
        )

        hint = str(props.get(args.outlet_hint_field) or "").strip()
        if hint:
            if hint not in node_index:
                raise ValueError(
                    f"Feature '{subcatchment_id}' has unknown outlet hint '{hint}' not found in network nodes"
                )
            outlet = hint
            nx, ny = node_index[outlet]
            outlet_distance = math.hypot(cx - nx, cy - ny)
            outlet_method = f"hint:{args.outlet_hint_field}"
        else:
            outlet, outlet_distance = nearest_node(cx, cy, node_index)
            outlet_method = "nearest_node"

        if args.max_link_distance_m is not None and outlet_distance > args.max_link_distance_m:
            raise ValueError(
                f"Feature '{subcatchment_id}' linked outlet '{outlet}' at distance {outlet_distance:.3f} m "
                f"exceeding --max-link-distance-m={args.max_link_distance_m}"
            )

        curb_length_m = parse_optional_float(props, "curb_length_m")
        if curb_length_m is None:
            curb_length_m = args.default_curb_length_m

        snow_pack = str(props.get("snow_pack") or "").strip()
        rain_gage = str(props.get("rain_gage") or args.default_rain_gage).strip()

        csv_row = {
            "subcatchment_id": subcatchment_id,
            "outlet": outlet,
            "area_ha": round(area_ha, 6),
            "width_m": round(width_m, 6),
            "slope_pct": round(slope_pct, 6),
            "curb_length_m": round(curb_length_m, 6),
            "snow_pack": snow_pack,
            "rain_gage": rain_gage,
        }
        csv_rows.append(csv_row)

        detail_rows.append(
            {
                "id": subcatchment_id,
                "area_m2": area_m2,
                "perimeter_m": perimeter_m,
                "centroid": {"x": cx, "y": cy},
                "flow_length_m": flow_length_m,
                "width_m": width_m,
                "slope_pct": slope_pct,
                "slope_source": slope_source,
                "outlet": outlet,
                "outlet_distance_m": outlet_distance,
                "outlet_method": outlet_method,
            }
        )

    csv_rows.sort(key=lambda r: str(r["subcatchment_id"]))
    detail_rows.sort(key=lambda r: str(r["id"]))

    csv_headers = [
        "subcatchment_id",
        "outlet",
        "area_ha",
        "width_m",
        "slope_pct",
        "curb_length_m",
        "snow_pack",
        "rain_gage",
    ]
    write_csv(args.out_csv, csv_rows, csv_headers)

    report = {
        "ok": True,
        "skill": "swmm-gis",
        "inputs": {
            "subcatchments_geojson": str(args.subcatchments_geojson),
            "network_json": str(args.network_json),
        },
        "assumptions": {
            "planar_coordinates": True,
            "coordinate_units": "meters",
            "width_formula": "width_m = max(min_width_m, 2 * area_m2 / perimeter_m)",
            "slope_priority": [
                "properties.slope_pct",
                "(properties.elev_mean_m - properties.elev_outlet_m) / flow_length_m * 100",
                "default_slope_pct",
            ],
            "outlet_link_priority": [
                f"properties.{args.outlet_hint_field}",
                "nearest network node",
            ],
        },
        "parameters": {
            "id_field": args.id_field,
            "outlet_hint_field": args.outlet_hint_field,
            "default_slope_pct": args.default_slope_pct,
            "min_slope_pct": args.min_slope_pct,
            "min_width_m": args.min_width_m,
            "default_curb_length_m": args.default_curb_length_m,
            "default_rain_gage": args.default_rain_gage,
            "max_link_distance_m": args.max_link_distance_m,
        },
        "counts": {
            "feature_count": len(features),
            "subcatchment_count": len(detail_rows),
            "network_node_count": len(node_index),
        },
        "subcatchments": detail_rows,
        "outputs": {
            "builder_csv": str(args.out_csv),
        },
    }
    write_json(args.out_json, report)

    print(
        json.dumps(
            {
                "ok": True,
                "out_csv": str(args.out_csv),
                "out_json": str(args.out_json),
                "subcatchment_count": len(detail_rows),
                "network_node_count": len(node_index),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
