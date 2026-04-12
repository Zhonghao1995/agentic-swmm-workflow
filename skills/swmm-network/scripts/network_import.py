#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def save_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "null"):
        return default
    return float(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y"}


def read_geojson_features(path: Path) -> list[dict]:
    obj = load_json(path)
    if obj.get("type") != "FeatureCollection":
        raise ValueError(f"Expected GeoJSON FeatureCollection: {path}")
    return obj.get("features", [])


def read_csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_value(props: dict, field: str | None, default: Any = None) -> Any:
    if not field:
        return default
    return props.get(field, default)


def import_junctions(path: Path, cfg: dict) -> list[dict]:
    fmt = cfg.get("format", "geojson")
    fields = cfg.get("fields", {})
    out = []
    if fmt == "geojson":
        for feat in read_geojson_features(path):
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [None, None])
            out.append({
                "id": str(get_value(props, fields.get("id"))),
                "invert_elev": _as_float(get_value(props, fields.get("invert_elev")), 0.0),
                "max_depth": _as_float(get_value(props, fields.get("max_depth")), 0.0),
                "init_depth": _as_float(get_value(props, fields.get("init_depth")), 0.0),
                "sur_depth": _as_float(get_value(props, fields.get("sur_depth")), 0.0),
                "aponded": _as_float(get_value(props, fields.get("aponded")), 0.0),
                "coordinates": {"x": float(coords[0]), "y": float(coords[1])},
            })
    elif fmt == "csv":
        for row in read_csv_rows(path):
            out.append({
                "id": str(get_value(row, fields.get("id"))),
                "invert_elev": _as_float(get_value(row, fields.get("invert_elev")), 0.0),
                "max_depth": _as_float(get_value(row, fields.get("max_depth")), 0.0),
                "init_depth": _as_float(get_value(row, fields.get("init_depth")), 0.0),
                "sur_depth": _as_float(get_value(row, fields.get("sur_depth")), 0.0),
                "aponded": _as_float(get_value(row, fields.get("aponded")), 0.0),
                "coordinates": {
                    "x": float(get_value(row, fields.get("x"))),
                    "y": float(get_value(row, fields.get("y"))),
                },
            })
    else:
        raise ValueError(f"Unsupported junction format: {fmt}")
    return out


def import_outfalls(path: Path, cfg: dict) -> list[dict]:
    fmt = cfg.get("format", "geojson")
    fields = cfg.get("fields", {})
    out = []
    if fmt == "geojson":
        for feat in read_geojson_features(path):
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [None, None])
            out.append({
                "id": str(get_value(props, fields.get("id"))),
                "invert_elev": _as_float(get_value(props, fields.get("invert_elev")), 0.0),
                "type": str(get_value(props, fields.get("type"), cfg.get("defaults", {}).get("type", "FREE"))),
                "stage_data": get_value(props, fields.get("stage_data")),
                "gated": _as_bool(get_value(props, fields.get("gated")), cfg.get("defaults", {}).get("gated", False)),
                "route_to": get_value(props, fields.get("route_to")),
                "coordinates": {"x": float(coords[0]), "y": float(coords[1])},
            })
    elif fmt == "csv":
        for row in read_csv_rows(path):
            out.append({
                "id": str(get_value(row, fields.get("id"))),
                "invert_elev": _as_float(get_value(row, fields.get("invert_elev")), 0.0),
                "type": str(get_value(row, fields.get("type"), cfg.get("defaults", {}).get("type", "FREE"))),
                "stage_data": get_value(row, fields.get("stage_data")),
                "gated": _as_bool(get_value(row, fields.get("gated")), cfg.get("defaults", {}).get("gated", False)),
                "route_to": get_value(row, fields.get("route_to")),
                "coordinates": {
                    "x": float(get_value(row, fields.get("x"))),
                    "y": float(get_value(row, fields.get("y"))),
                },
            })
    else:
        raise ValueError(f"Unsupported outfall format: {fmt}")
    return out


def import_conduits(path: Path, cfg: dict) -> list[dict]:
    fmt = cfg.get("format", "geojson")
    if fmt != "geojson":
        raise ValueError("MVP conduit import currently supports geojson only")
    fields = cfg.get("fields", {})
    defaults = cfg.get("defaults", {})
    out = []
    for feat in read_geojson_features(path):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        vertices = []
        if geom.get("type") == "LineString" and len(coords) > 2:
            for xy in coords[1:-1]:
                vertices.append({"x": float(xy[0]), "y": float(xy[1])})
        out.append({
            "id": str(get_value(props, fields.get("id"))),
            "from_node": str(get_value(props, fields.get("from_node"))),
            "to_node": str(get_value(props, fields.get("to_node"))),
            "length": _as_float(get_value(props, fields.get("length")), defaults.get("length", 1.0)),
            "roughness": _as_float(get_value(props, fields.get("roughness")), defaults.get("roughness", 0.013)),
            "in_offset": _as_float(get_value(props, fields.get("in_offset")), defaults.get("in_offset", 0.0)),
            "out_offset": _as_float(get_value(props, fields.get("out_offset")), defaults.get("out_offset", 0.0)),
            "init_flow": _as_float(get_value(props, fields.get("init_flow")), defaults.get("init_flow", 0.0)),
            "max_flow": _as_float(get_value(props, fields.get("max_flow")), defaults.get("max_flow")),
            "xsection": {
                "shape": str(get_value(props, fields.get("shape"), defaults.get("shape", "CIRCULAR"))),
                "geom1": _as_float(get_value(props, fields.get("geom1") or fields.get("diameter")), defaults.get("geom1", 0.5)),
                "geom2": _as_float(get_value(props, fields.get("geom2")), defaults.get("geom2", 0.0)),
                "geom3": _as_float(get_value(props, fields.get("geom3")), defaults.get("geom3", 0.0)),
                "geom4": _as_float(get_value(props, fields.get("geom4")), defaults.get("geom4", 0.0)),
                "barrels": int(get_value(props, fields.get("barrels"), defaults.get("barrels", 1))),
            },
            **({"vertices": vertices} if vertices else {}),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conduits", type=Path, required=True)
    ap.add_argument("--junctions", type=Path, required=True)
    ap.add_argument("--outfalls", type=Path, required=True)
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    mapping = load_json(args.mapping)
    network = {
        "meta": mapping.get("meta", {}),
        "junctions": import_junctions(args.junctions, mapping["junctions"]),
        "outfalls": import_outfalls(args.outfalls, mapping["outfalls"]),
        "conduits": import_conduits(args.conduits, mapping["conduits"]),
    }
    save_json(args.out, network)
    print(json.dumps({
        "ok": True,
        "out": str(args.out),
        "summary": {
            "junction_count": len(network["junctions"]),
            "outfall_count": len(network["outfalls"]),
            "conduit_count": len(network["conduits"]),
        }
    }, indent=2))


if __name__ == "__main__":
    main()
