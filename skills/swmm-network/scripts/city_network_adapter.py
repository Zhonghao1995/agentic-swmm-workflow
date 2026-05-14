#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def sha256_file(path: Path | None) -> str | None:
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_geojson_features(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    obj = load_json(path)
    if obj.get("type") != "FeatureCollection":
        raise ValueError(f"Expected GeoJSON FeatureCollection: {path}")
    return list(obj.get("features") or [])


def as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    return float(str(value).strip())


def as_int(value: Any, default: int = 1) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(float(str(value).strip()))


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def field_value(props: dict[str, Any], fields: dict[str, str | None], key: str, default: Any = None) -> Any:
    field = fields.get(key)
    if not field:
        return default
    return props.get(field, default)


def coord_key(x: float, y: float, precision: int) -> str:
    return f"{round(x, precision):.{precision}f},{round(y, precision):.{precision}f}"


def default_node_id(prefix: str, x: float, y: float, precision: int) -> str:
    token = coord_key(x, y, precision).replace("-", "m").replace(".", "p").replace(",", "_")
    return f"{prefix}_{token}"


def line_length(coords: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        total += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    return total


def csv_pipe_geometry(row: dict[str, Any], fields: dict[str, str | None]) -> list[list[float]]:
    required = ["from_x", "from_y", "to_x", "to_y"]
    if not all(fields.get(k) and text(row.get(fields[k])) for k in required):
        return []
    return [
        [float(row[str(fields["from_x"])]), float(row[str(fields["from_y"])])],
        [float(row[str(fields["to_x"])]), float(row[str(fields["to_y"])])],
    ]


def geojson_pipe_geometry(feature: dict[str, Any]) -> list[list[float]]:
    geom = feature.get("geometry") or {}
    if geom.get("type") != "LineString":
        raise ValueError("Pipe GeoJSON features must use LineString geometry")
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        raise ValueError("Pipe LineString must contain at least two coordinates")
    return [[float(xy[0]), float(xy[1])] for xy in coords]


def point_geometry(feature: dict[str, Any]) -> tuple[float, float]:
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Point":
        raise ValueError("Node/outfall GeoJSON features must use Point geometry")
    coords = geom.get("coordinates") or []
    return float(coords[0]), float(coords[1])


def csv_point_geometry(row: dict[str, Any], fields: dict[str, str | None]) -> tuple[float, float]:
    x_field = fields.get("x")
    y_field = fields.get("y")
    if not x_field or not y_field:
        raise ValueError("CSV node/outfall mapping requires x and y fields")
    return float(row[x_field]), float(row[y_field])


def load_point_assets(
    *,
    csv_path: Path | None,
    geojson_path: Path | None,
    cfg: dict[str, Any],
    asset_kind: str,
) -> list[dict[str, Any]]:
    fields = cfg.get("fields", {})
    defaults = cfg.get("defaults", {})
    rows = [{"props": row, "xy": csv_point_geometry(row, fields), "source": str(csv_path)} for row in read_csv_rows(csv_path)]
    rows.extend(
        {"props": feat.get("properties") or {}, "xy": point_geometry(feat), "source": str(geojson_path)}
        for feat in read_geojson_features(geojson_path)
    )

    out = []
    for idx, rec in enumerate(rows, start=1):
        props = rec["props"]
        x, y = rec["xy"]
        node_id = text(field_value(props, fields, "id"), f"{asset_kind.upper()}_{idx}")
        common = {
            "id": node_id,
            "invert_elev": as_float(field_value(props, fields, "invert_elev"), defaults.get("invert_elev", 0.0)),
            "coordinates": {"x": x, "y": y},
            "source_asset": {
                "kind": asset_kind,
                "source": rec["source"],
                "source_id": text(field_value(props, fields, "source_id"), node_id),
            },
        }
        if asset_kind == "outfall":
            out.append(
                {
                    **common,
                    "type": text(field_value(props, fields, "type"), defaults.get("type", "FREE")).upper(),
                    "stage_data": field_value(props, fields, "stage_data", defaults.get("stage_data")),
                    "gated": as_bool(field_value(props, fields, "gated"), defaults.get("gated", False)),
                    "route_to": field_value(props, fields, "route_to", defaults.get("route_to")),
                    "asset_type": text(field_value(props, fields, "asset_type"), defaults.get("asset_type", "storm")),
                    "system_layer": text(field_value(props, fields, "system_layer"), defaults.get("system_layer", "minor_pipe")),
                }
            )
        else:
            out.append(
                {
                    **common,
                    "max_depth": as_float(field_value(props, fields, "max_depth"), defaults.get("max_depth", 2.0)),
                    "init_depth": as_float(field_value(props, fields, "init_depth"), defaults.get("init_depth", 0.0)),
                    "sur_depth": as_float(field_value(props, fields, "sur_depth"), defaults.get("sur_depth", 0.0)),
                    "aponded": as_float(field_value(props, fields, "aponded"), defaults.get("aponded", 0.0)),
                    "asset_type": text(field_value(props, fields, "asset_type"), defaults.get("asset_type", "storm")),
                    "system_layer": text(field_value(props, fields, "system_layer"), defaults.get("system_layer", "minor_pipe")),
                }
            )
    return out


def collect_pipe_records(csv_path: Path | None, geojson_path: Path | None, fields: dict[str, str | None]) -> list[dict[str, Any]]:
    records = []
    for row in read_csv_rows(csv_path):
        records.append({"props": row, "coords": csv_pipe_geometry(row, fields), "source": str(csv_path)})
    for feat in read_geojson_features(geojson_path):
        records.append({"props": feat.get("properties") or {}, "coords": geojson_pipe_geometry(feat), "source": str(geojson_path)})
    return records


def add_inferred_node(
    *,
    nodes_by_id: dict[str, dict[str, Any]],
    coord_to_id: dict[str, str],
    node_id: str,
    x: float,
    y: float,
    invert_elev: float,
    max_depth: float,
    precision: int,
    source_pipe: str,
    system_layer: str,
    asset_type: str,
) -> None:
    key = coord_key(x, y, precision)
    if node_id in nodes_by_id:
        coord_to_id.setdefault(key, node_id)
        return
    nodes_by_id[node_id] = {
        "id": node_id,
        "invert_elev": invert_elev,
        "max_depth": max_depth,
        "init_depth": 0.0,
        "sur_depth": 0.0,
        "aponded": 0.0,
        "coordinates": {"x": x, "y": y},
        "asset_type": asset_type,
        "system_layer": system_layer,
        "inferred": True,
        "source_asset": {
            "kind": "inferred_junction",
            "source_pipe": source_pipe,
            "coordinate_key": key,
        },
    }
    coord_to_id[key] = node_id


def build_network(args: argparse.Namespace, mapping: dict[str, Any]) -> dict[str, Any]:
    precision = int(mapping.get("coordinate_precision", 3))
    pipes_cfg = mapping.get("pipes", {})
    node_cfg = mapping.get("junctions", {})
    outfall_cfg = mapping.get("outfalls", {})
    pipe_fields = pipes_cfg.get("fields", {})
    pipe_defaults = pipes_cfg.get("defaults", {})
    infer_cfg = mapping.get("inference", {})

    explicit_junctions = load_point_assets(
        csv_path=args.junctions_csv,
        geojson_path=args.junctions_geojson,
        cfg=node_cfg,
        asset_kind="junction",
    )
    explicit_outfalls = load_point_assets(
        csv_path=args.outfalls_csv,
        geojson_path=args.outfalls_geojson,
        cfg=outfall_cfg,
        asset_kind="outfall",
    )

    nodes_by_id = {str(j["id"]): j for j in explicit_junctions}
    outfalls_by_id = {str(o["id"]): o for o in explicit_outfalls}
    coord_to_id: dict[str, str] = {}
    for rec in explicit_junctions + explicit_outfalls:
        xy = rec["coordinates"]
        coord_to_id[coord_key(float(xy["x"]), float(xy["y"]), precision)] = str(rec["id"])

    pipe_records = collect_pipe_records(args.pipes_csv, args.pipes_geojson, pipe_fields)
    conduits = []
    inferred_count = 0
    for idx, rec in enumerate(pipe_records, start=1):
        props = rec["props"]
        coords = rec["coords"]
        if not coords:
            raise ValueError(f"Pipe record {idx} has no geometry or endpoint coordinate fields")
        start = coords[0]
        end = coords[-1]
        start_key = coord_key(float(start[0]), float(start[1]), precision)
        end_key = coord_key(float(end[0]), float(end[1]), precision)
        pipe_id = text(field_value(props, pipe_fields, "id"), f"P{idx}")
        system_layer = text(field_value(props, pipe_fields, "system_layer"), pipe_defaults.get("system_layer", "minor_pipe"))
        asset_type = text(field_value(props, pipe_fields, "asset_type"), pipe_defaults.get("asset_type", "storm"))
        from_node = text(field_value(props, pipe_fields, "from_node"), coord_to_id.get(start_key, ""))
        to_node = text(field_value(props, pipe_fields, "to_node"), coord_to_id.get(end_key, ""))

        if not from_node:
            from_node = default_node_id(str(infer_cfg.get("junction_prefix", "J")), float(start[0]), float(start[1]), precision)
        if not to_node:
            to_node = default_node_id(str(infer_cfg.get("junction_prefix", "J")), float(end[0]), float(end[1]), precision)

        from_invert = as_float(field_value(props, pipe_fields, "from_invert_elev"), pipe_defaults.get("from_invert_elev", 0.0))
        to_invert = as_float(field_value(props, pipe_fields, "to_invert_elev"), pipe_defaults.get("to_invert_elev", from_invert))
        max_depth = float(infer_cfg.get("max_depth", 2.0))
        if from_node not in outfalls_by_id and from_node not in nodes_by_id:
            inferred_count += 1
            add_inferred_node(
                nodes_by_id=nodes_by_id,
                coord_to_id=coord_to_id,
                node_id=from_node,
                x=float(start[0]),
                y=float(start[1]),
                invert_elev=float(from_invert or 0.0),
                max_depth=max_depth,
                precision=precision,
                source_pipe=pipe_id,
                system_layer=system_layer,
                asset_type=asset_type,
            )
        if to_node not in outfalls_by_id and to_node not in nodes_by_id:
            inferred_count += 1
            add_inferred_node(
                nodes_by_id=nodes_by_id,
                coord_to_id=coord_to_id,
                node_id=to_node,
                x=float(end[0]),
                y=float(end[1]),
                invert_elev=float(to_invert or 0.0),
                max_depth=max_depth,
                precision=precision,
                source_pipe=pipe_id,
                system_layer=system_layer,
                asset_type=asset_type,
            )

        vertices = [{"x": float(x), "y": float(y)} for x, y in coords[1:-1]]
        length = as_float(field_value(props, pipe_fields, "length"), None)
        if length is None:
            length = max(line_length(coords), float(pipe_defaults.get("minimum_length", 1.0)))
        geom1 = as_float(
            field_value(props, pipe_fields, "geom1", field_value(props, pipe_fields, "diameter")),
            pipe_defaults.get("geom1", 0.5),
        )
        conduits.append(
            {
                "id": pipe_id,
                "from_node": from_node,
                "to_node": to_node,
                "length": length,
                "roughness": as_float(field_value(props, pipe_fields, "roughness"), pipe_defaults.get("roughness", 0.013)),
                "in_offset": as_float(field_value(props, pipe_fields, "in_offset"), pipe_defaults.get("in_offset", 0.0)),
                "out_offset": as_float(field_value(props, pipe_fields, "out_offset"), pipe_defaults.get("out_offset", 0.0)),
                "init_flow": as_float(field_value(props, pipe_fields, "init_flow"), pipe_defaults.get("init_flow", 0.0)),
                "max_flow": as_float(field_value(props, pipe_fields, "max_flow"), pipe_defaults.get("max_flow")),
                "xsection": {
                    "shape": text(field_value(props, pipe_fields, "shape"), pipe_defaults.get("shape", "CIRCULAR")).upper(),
                    "geom1": geom1,
                    "geom2": as_float(field_value(props, pipe_fields, "geom2"), pipe_defaults.get("geom2", 0.0)),
                    "geom3": as_float(field_value(props, pipe_fields, "geom3"), pipe_defaults.get("geom3", 0.0)),
                    "geom4": as_float(field_value(props, pipe_fields, "geom4"), pipe_defaults.get("geom4", 0.0)),
                    "barrels": as_int(field_value(props, pipe_fields, "barrels"), int(pipe_defaults.get("barrels", 1))),
                },
                "vertices": vertices,
                "asset_type": asset_type,
                "system_layer": system_layer,
                "material": text(field_value(props, pipe_fields, "material"), pipe_defaults.get("material", "")),
                "source_asset": {
                    "kind": "pipe",
                    "source": rec["source"],
                    "source_id": text(field_value(props, pipe_fields, "source_id"), pipe_id),
                },
            }
        )

    layers = sorted({c.get("system_layer", "") for c in conduits if c.get("system_layer")})
    network = {
        "meta": {
            **(mapping.get("meta") or {}),
            "adapter": "city_network_adapter",
            "dual_system_ready": bool(mapping.get("dual_system_ready", True)),
            "system_layers": layers,
            "sources": {
                "pipes_csv": str(args.pipes_csv) if args.pipes_csv else None,
                "pipes_geojson": str(args.pipes_geojson) if args.pipes_geojson else None,
                "junctions_csv": str(args.junctions_csv) if args.junctions_csv else None,
                "junctions_geojson": str(args.junctions_geojson) if args.junctions_geojson else None,
                "outfalls_csv": str(args.outfalls_csv) if args.outfalls_csv else None,
                "outfalls_geojson": str(args.outfalls_geojson) if args.outfalls_geojson else None,
            },
            "source_hashes": {
                "pipes_csv": sha256_file(args.pipes_csv),
                "pipes_geojson": sha256_file(args.pipes_geojson),
                "junctions_csv": sha256_file(args.junctions_csv),
                "junctions_geojson": sha256_file(args.junctions_geojson),
                "outfalls_csv": sha256_file(args.outfalls_csv),
                "outfalls_geojson": sha256_file(args.outfalls_geojson),
            },
            "counts": {
                "pipes": len(conduits),
                "explicit_junctions": len(explicit_junctions),
                "explicit_outfalls": len(explicit_outfalls),
                "inferred_junctions": inferred_count,
            },
        },
        "junctions": sorted(nodes_by_id.values(), key=lambda x: str(x["id"])),
        "outfalls": sorted(outfalls_by_id.values(), key=lambda x: str(x["id"])),
        "conduits": conduits,
    }
    return network


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert structured urban pipe/surface asset exports into Agentic SWMM network.json."
    )
    ap.add_argument("--pipes-csv", type=Path, default=None)
    ap.add_argument("--pipes-geojson", type=Path, default=None)
    ap.add_argument("--junctions-csv", type=Path, default=None)
    ap.add_argument("--junctions-geojson", type=Path, default=None)
    ap.add_argument("--outfalls-csv", type=Path, default=None)
    ap.add_argument("--outfalls-geojson", type=Path, default=None)
    ap.add_argument("--mapping-json", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if args.pipes_csv is None and args.pipes_geojson is None:
        raise ValueError("At least one pipe source is required: --pipes-csv or --pipes-geojson")

    mapping = load_json(args.mapping_json)
    network = build_network(args, mapping)
    save_json(args.out, network)
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(args.out),
                "junction_count": len(network["junctions"]),
                "outfall_count": len(network["outfalls"]),
                "conduit_count": len(network["conduits"]),
                "system_layers": network["meta"]["system_layers"],
                "inferred_junctions": network["meta"]["counts"]["inferred_junctions"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
