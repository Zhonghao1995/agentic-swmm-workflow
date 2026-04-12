#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


FloatCandidate = tuple[str, dict[str, Any], str]


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


def parse_optional_float_value(raw: Any, *, context: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"Invalid float in '{context}': {raw}")
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid float in '{context}': {raw}") from exc


def parse_optional_float(mapping: dict[str, Any], field: str, *, mapping_name: str = "properties") -> float | None:
    if field not in mapping:
        return None
    return parse_optional_float_value(mapping.get(field), context=f"{mapping_name}.{field}")


def pick_first_float(candidates: list[FloatCandidate]) -> tuple[float, str] | None:
    for mapping_name, mapping, field in candidates:
        value = parse_optional_float(mapping, field, mapping_name=mapping_name)
        if value is None:
            continue
        return value, f"{mapping_name}:{field}"
    return None


def normalize_non_blank(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def increment_count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def first_present_id(record: dict[str, Any], fields: list[str], *, context: str) -> str:
    for field in fields:
        value = normalize_non_blank(record.get(field))
        if value is not None:
            return value
    raise ValueError(f"{context} missing id field; tried {fields}")


def build_dem_stats_index(
    dem_stats_json: Path | None,
    *,
    id_field: str,
    dem_stats_id_field: str,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if dem_stats_json is None:
        return {}, None

    raw = load_json(dem_stats_json)
    candidates = [dem_stats_id_field, id_field, "subcatchment_id", "id"]

    records: list[tuple[str, dict[str, Any]]] = []

    def append_record(record_id: str, record: dict[str, Any], *, context: str) -> None:
        if not isinstance(record, dict):
            raise ValueError(f"{context} must be an object")
        rid = normalize_non_blank(record_id)
        if rid is None:
            rid = first_present_id(record, candidates, context=context)
        records.append((rid, record))

    if isinstance(raw, dict) and "subcatchments" in raw:
        body = raw.get("subcatchments")
        if isinstance(body, list):
            for idx, entry in enumerate(body, start=1):
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"DEM stats entry {idx} in {dem_stats_json} must be an object"
                    )
                rid = first_present_id(entry, candidates, context=f"DEM stats entry {idx}")
                records.append((rid, entry))
        elif isinstance(body, dict):
            for key, entry in body.items():
                append_record(str(key), entry, context=f"DEM stats entry '{key}'")
        else:
            raise ValueError(
                f"DEM stats file {dem_stats_json} field 'subcatchments' must be a list or object"
            )
    elif isinstance(raw, list):
        for idx, entry in enumerate(raw, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"DEM stats entry {idx} in {dem_stats_json} must be an object")
            rid = first_present_id(entry, candidates, context=f"DEM stats entry {idx}")
            records.append((rid, entry))
    elif isinstance(raw, dict) and all(isinstance(v, dict) for v in raw.values()):
        for key, entry in raw.items():
            append_record(str(key), entry, context=f"DEM stats entry '{key}'")
    else:
        raise ValueError(
            "DEM stats JSON must be one of: list[object], "
            "{'subcatchments': list|object}, or object keyed by subcatchment id"
        )

    out: dict[str, dict[str, Any]] = {}
    for rid, entry in records:
        if rid in out:
            raise ValueError(f"Duplicate DEM stats id '{rid}' in {dem_stats_json}")
        out[rid] = dict(entry)

    return out, str(dem_stats_json)


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


def clamp_with_min(
    value: float,
    *,
    min_value: float,
    source: str,
    metric: str,
    diagnostics: list[str],
) -> tuple[float, str]:
    if value >= min_value:
        return value, source
    diagnostics.append(
        f"{metric} from {source}={value:.6f} below min {min_value:.6f}; clamped."
    )
    return min_value, f"{source}|clamped_min"


def resolve_dem_flow_length(
    props: dict[str, Any],
    dem_stats: dict[str, Any],
    diagnostics: list[str],
) -> tuple[float, str] | None:
    dem_flow = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_flow_length_m"),
            ("dem_stats", dem_stats, "flow_length_m"),
            ("properties", props, "dem_flow_length_m"),
            ("properties", props, "flow_length_m"),
        ]
    )
    if dem_flow is None:
        return None

    value, source = dem_flow
    if value <= 0:
        diagnostics.append(f"Ignoring non-positive flow length from {source}={value:.6f}.")
        return None
    return value, source


def estimate_width_m(
    area_m2: float,
    perimeter_m: float,
    *,
    min_width_m: float,
    props: dict[str, Any],
    dem_stats: dict[str, Any],
    dem_flow_length: tuple[float, str] | None,
    diagnostics: list[str],
) -> tuple[float, str]:
    width_direct = pick_first_float(
        [
            ("properties", props, "width_m"),
            ("properties", props, "hydraulic_width_m"),
            ("dem_stats", dem_stats, "dem_width_m"),
            ("dem_stats", dem_stats, "width_m"),
        ]
    )
    if width_direct is not None:
        width, source = width_direct
        if width > 0:
            return clamp_with_min(
                width,
                min_value=min_width_m,
                source=source,
                metric="width_m",
                diagnostics=diagnostics,
            )
        diagnostics.append(f"Ignoring non-positive width from {source}={width:.6f}.")

    if dem_flow_length is not None:
        flow_length_m, flow_source = dem_flow_length
        width = area_m2 / max(flow_length_m, 1e-9)
        width, width_source = clamp_with_min(
            width,
            min_value=min_width_m,
            source=f"derived:area_m2/{flow_source}",
            metric="width_m",
            diagnostics=diagnostics,
        )
        return width, width_source

    # Deterministic surrogate: equivalent hydraulic width from area and perimeter.
    width_geom = 2.0 * area_m2 / max(perimeter_m, 1e-9)
    width_geom, width_source = clamp_with_min(
        width_geom,
        min_value=min_width_m,
        source="derived:2*area_m2/perimeter_m",
        metric="width_m",
        diagnostics=diagnostics,
    )
    return width_geom, width_source


def estimate_flow_length_m(
    area_m2: float,
    width_m: float,
    *,
    width_source: str,
    dem_flow_length: tuple[float, str] | None,
) -> tuple[float, str]:
    if dem_flow_length is not None:
        value, source = dem_flow_length
        return value, source
    return area_m2 / max(width_m, 1e-9), f"derived:area_m2/width_m ({width_source})"


def estimate_slope_pct(
    props: dict[str, Any],
    dem_stats: dict[str, Any],
    flow_length_m: float,
    *,
    flow_length_source: str,
    default_slope_pct: float,
    min_slope_pct: float,
    diagnostics: list[str],
) -> tuple[float, str]:
    slope_direct = pick_first_float([("properties", props, "slope_pct")])
    if slope_direct is not None:
        slope, source = slope_direct
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=source,
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    dem_slope_direct = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_slope_pct"),
            ("dem_stats", dem_stats, "raster_slope_pct"),
            ("dem_stats", dem_stats, "mean_slope_pct"),
            ("dem_stats", dem_stats, "slope_pct"),
            ("properties", props, "dem_slope_pct"),
            ("properties", props, "raster_slope_pct"),
        ]
    )
    if dem_slope_direct is not None:
        slope, source = dem_slope_direct
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=source,
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    dem_mean = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_elev_mean_m"),
            ("dem_stats", dem_stats, "elev_mean_m"),
            ("properties", props, "dem_elev_mean_m"),
        ]
    )
    dem_outlet = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_elev_outlet_m"),
            ("dem_stats", dem_stats, "elev_outlet_m"),
            ("properties", props, "dem_elev_outlet_m"),
        ]
    )
    if dem_mean is not None and dem_outlet is not None:
        mean_value, mean_source = dem_mean
        outlet_value, outlet_source = dem_outlet
        slope = (mean_value - outlet_value) / max(flow_length_m, 1e-9) * 100.0
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=f"derived:({mean_source}-{outlet_source})/{flow_length_source}*100",
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    dem_max = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_elev_max_m"),
            ("dem_stats", dem_stats, "elev_max_m"),
            ("properties", props, "dem_elev_max_m"),
        ]
    )
    dem_min = pick_first_float(
        [
            ("dem_stats", dem_stats, "dem_elev_min_m"),
            ("dem_stats", dem_stats, "elev_min_m"),
            ("properties", props, "dem_elev_min_m"),
        ]
    )
    if dem_max is not None and dem_min is not None:
        max_value, max_source = dem_max
        min_value, min_source = dem_min
        slope = (max_value - min_value) / max(flow_length_m, 1e-9) * 100.0
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=f"derived:({max_source}-{min_source})/{flow_length_source}*100",
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    if dem_mean is not None and dem_min is not None:
        mean_value, mean_source = dem_mean
        min_value, min_source = dem_min
        slope = (mean_value - min_value) / max(flow_length_m, 1e-9) * 100.0
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=f"derived:({mean_source}-{min_source})/{flow_length_source}*100",
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    elev_mean = pick_first_float([("properties", props, "elev_mean_m")])
    elev_outlet = pick_first_float([("properties", props, "elev_outlet_m")])
    if elev_mean is not None and elev_outlet is not None:
        mean_value, mean_source = elev_mean
        outlet_value, outlet_source = elev_outlet
        slope = (mean_value - outlet_value) / max(flow_length_m, 1e-9) * 100.0
        return clamp_with_min(
            slope,
            min_value=min_slope_pct,
            source=f"derived:({mean_source}-{outlet_source})/{flow_length_source}*100",
            metric="slope_pct",
            diagnostics=diagnostics,
        )

    return clamp_with_min(
        default_slope_pct,
        min_value=min_slope_pct,
        source="default_slope_pct",
        metric="slope_pct",
        diagnostics=diagnostics,
    )


def link_outlet(
    *,
    subcatchment_id: str,
    props: dict[str, Any],
    outlet_hint_field: str,
    centroid_x: float,
    centroid_y: float,
    node_index: dict[str, tuple[float, float]],
) -> tuple[str, float, str, str, list[str]]:
    diagnostics: list[str] = []
    hint = str(props.get(outlet_hint_field) or "").strip()

    if hint:
        if hint in node_index:
            nx, ny = node_index[hint]
            outlet_distance = math.hypot(centroid_x - nx, centroid_y - ny)
            return hint, outlet_distance, f"hint:{outlet_hint_field}", hint, diagnostics

        diagnostics.append(
            f"Feature '{subcatchment_id}' has unknown outlet hint '{hint}' in "
            f"properties.{outlet_hint_field}; used nearest node fallback."
        )
        outlet, outlet_distance = nearest_node(centroid_x, centroid_y, node_index)
        return (
            outlet,
            outlet_distance,
            f"nearest_node_fallback:invalid_hint:{outlet_hint_field}",
            hint,
            diagnostics,
        )

    diagnostics.append(
        f"Feature '{subcatchment_id}' has blank properties.{outlet_hint_field}; used nearest node fallback."
    )
    outlet, outlet_distance = nearest_node(centroid_x, centroid_y, node_index)
    return outlet, outlet_distance, "nearest_node", "", diagnostics


def is_dem_assisted_source(source: str) -> bool:
    token = source.lower()
    return "dem_stats" in token or "dem_" in token or "raster" in token


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Preprocess subcatchment polygons into builder-ready CSV with deterministic "
            "width/slope/outlet linking and optional DEM-assisted metrics."
        )
    )
    ap.add_argument("--subcatchments-geojson", type=Path, required=True)
    ap.add_argument("--network-json", type=Path, required=True)
    ap.add_argument("--out-csv", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--id-field", default="subcatchment_id")
    ap.add_argument("--outlet-hint-field", default="outlet_hint")
    ap.add_argument("--dem-stats-json", type=Path, default=None)
    ap.add_argument("--dem-stats-id-field", default="subcatchment_id")
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
    dem_stats_index, dem_stats_source = build_dem_stats_index(
        args.dem_stats_json,
        id_field=args.id_field,
        dem_stats_id_field=args.dem_stats_id_field,
    )

    seen_ids: set[str] = set()
    used_dem_ids: set[str] = set()
    diagnostics: list[dict[str, str]] = []
    csv_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    width_source_counts: dict[str, int] = {}
    slope_source_counts: dict[str, int] = {}
    outlet_method_counts: dict[str, int] = {}

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

        dem_stats = dem_stats_index.get(subcatchment_id, {})
        if dem_stats:
            used_dem_ids.add(subcatchment_id)

        row_diagnostics: list[str] = []
        if args.dem_stats_json is not None and not dem_stats:
            row_diagnostics.append(
                f"No DEM stats record matched id '{subcatchment_id}'; using deterministic fallback where needed."
            )

        area_m2, cx, cy, perimeter_m = geometry_metrics(geom)
        area_ha = area_m2 / 10000.0

        dem_flow_length = resolve_dem_flow_length(props, dem_stats, row_diagnostics)
        width_m, width_source = estimate_width_m(
            area_m2,
            perimeter_m,
            min_width_m=args.min_width_m,
            props=props,
            dem_stats=dem_stats,
            dem_flow_length=dem_flow_length,
            diagnostics=row_diagnostics,
        )
        flow_length_m, flow_length_source = estimate_flow_length_m(
            area_m2,
            width_m,
            width_source=width_source,
            dem_flow_length=dem_flow_length,
        )
        slope_pct, slope_source = estimate_slope_pct(
            props,
            dem_stats,
            flow_length_m,
            flow_length_source=flow_length_source,
            default_slope_pct=args.default_slope_pct,
            min_slope_pct=args.min_slope_pct,
            diagnostics=row_diagnostics,
        )

        outlet, outlet_distance, outlet_method, outlet_hint, outlet_diag = link_outlet(
            subcatchment_id=subcatchment_id,
            props=props,
            outlet_hint_field=args.outlet_hint_field,
            centroid_x=cx,
            centroid_y=cy,
            node_index=node_index,
        )
        row_diagnostics.extend(outlet_diag)

        if args.max_link_distance_m is not None and outlet_distance > args.max_link_distance_m:
            raise ValueError(
                f"Feature '{subcatchment_id}' linked outlet '{outlet}' via {outlet_method} at distance "
                f"{outlet_distance:.3f} m exceeding --max-link-distance-m={args.max_link_distance_m}"
            )

        curb_length_m = parse_optional_float(props, "curb_length_m")
        if curb_length_m is None:
            curb_length_m = args.default_curb_length_m

        snow_pack = str(props.get("snow_pack") or "").strip()
        rain_gage = str(props.get("rain_gage") or args.default_rain_gage).strip()

        dem_assisted = bool(dem_stats) and (
            is_dem_assisted_source(width_source)
            or is_dem_assisted_source(flow_length_source)
            or is_dem_assisted_source(slope_source)
        )

        increment_count(width_source_counts, width_source)
        increment_count(slope_source_counts, slope_source)
        increment_count(outlet_method_counts, outlet_method)

        for message in row_diagnostics:
            diagnostics.append({"id": subcatchment_id, "message": message})

        csv_row = {
            "subcatchment_id": subcatchment_id,
            "outlet": outlet,
            "area_ha": round(area_ha, 6),
            "width_m": round(width_m, 6),
            "slope_pct": round(slope_pct, 6),
            "curb_length_m": round(curb_length_m, 6),
            "snow_pack": snow_pack,
            "rain_gage": rain_gage,
            "area_source": "geometry:planar_polygon",
            "width_source": width_source,
            "flow_length_source": flow_length_source,
            "slope_source": slope_source,
            "outlet_method": outlet_method,
            "outlet_distance_m": round(outlet_distance, 6),
            "outlet_hint": outlet_hint,
            "outlet_diagnostic": " | ".join(outlet_diag),
            "dem_assisted": "yes" if dem_assisted else "no",
        }
        csv_rows.append(csv_row)

        detail_rows.append(
            {
                "id": subcatchment_id,
                "area_m2": area_m2,
                "perimeter_m": perimeter_m,
                "centroid": {"x": cx, "y": cy},
                "flow_length_m": flow_length_m,
                "flow_length_source": flow_length_source,
                "width_m": width_m,
                "width_source": width_source,
                "slope_pct": slope_pct,
                "slope_source": slope_source,
                "outlet": outlet,
                "outlet_distance_m": outlet_distance,
                "outlet_method": outlet_method,
                "outlet_hint": outlet_hint,
                "dem_stats_used": bool(dem_stats),
                "dem_assisted": dem_assisted,
                "diagnostics": row_diagnostics,
                "sources": {
                    "area": "geometry:planar_polygon",
                    "width": width_source,
                    "flow_length": flow_length_source,
                    "slope": slope_source,
                    "outlet": outlet_method,
                },
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
        "area_source",
        "width_source",
        "flow_length_source",
        "slope_source",
        "outlet_method",
        "outlet_distance_m",
        "outlet_hint",
        "outlet_diagnostic",
        "dem_assisted",
    ]
    write_csv(args.out_csv, csv_rows, csv_headers)

    unmatched_dem_ids = sorted(set(dem_stats_index) - used_dem_ids)
    for dem_id in unmatched_dem_ids:
        diagnostics.append(
            {
                "id": dem_id,
                "message": "DEM stats record did not match any subcatchment feature id.",
            }
        )

    report = {
        "ok": True,
        "skill": "swmm-gis",
        "inputs": {
            "subcatchments_geojson": str(args.subcatchments_geojson),
            "network_json": str(args.network_json),
            "dem_stats_json": dem_stats_source,
        },
        "assumptions": {
            "planar_coordinates": True,
            "coordinate_units": "meters",
            "width_priority": [
                "properties.width_m / properties.hydraulic_width_m",
                "DEM flow length -> area_m2 / flow_length_m",
                "2 * area_m2 / perimeter_m",
            ],
            "flow_length_priority": [
                "DEM flow length fields",
                "area_m2 / width_m",
            ],
            "slope_priority": [
                "properties.slope_pct",
                "DEM direct slope fields",
                "(DEM elevation stats) / flow_length_m",
                "(properties.elev_mean_m - properties.elev_outlet_m) / flow_length_m * 100",
                "default_slope_pct",
            ],
            "outlet_link_priority": [
                f"properties.{args.outlet_hint_field} (if valid)",
                "nearest network node fallback",
            ],
        },
        "parameters": {
            "id_field": args.id_field,
            "outlet_hint_field": args.outlet_hint_field,
            "dem_stats_id_field": args.dem_stats_id_field,
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
            "dem_stats_count": len(dem_stats_index),
            "dem_stats_matched_count": len(used_dem_ids),
            "diagnostic_count": len(diagnostics),
        },
        "method_counts": {
            "width_source": width_source_counts,
            "slope_source": slope_source_counts,
            "outlet_method": outlet_method_counts,
        },
        "diagnostics": diagnostics,
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
                "dem_stats_count": len(dem_stats_index),
                "dem_stats_matched_count": len(used_dem_ids),
                "diagnostic_count": len(diagnostics),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
