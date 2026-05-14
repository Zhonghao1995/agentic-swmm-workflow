#!/usr/bin/env python3
"""Convert a municipal basin shapefile into SWMM-ready subcatchments.

The cold-start agent and the operator baseline both had to hand-pick one
or more polygons from a raw DrainageBasinBoundary shapefile, attach a
``subcatchment_id`` field, compute area, and synthesise width and slope.
This tool packages that into a single MCP-callable step.

Selection strategies (the ``--mode`` flag):

- ``by_id_field``: select features where ``--id-field`` equals
  ``--id-value`` (e.g. OBJECTID = 100). Single match expected.
- ``by_index``: select the feature at ``--index`` (0-based) in the
  layer's iteration order.
- ``largest``: select the single feature with the largest projected area.
- ``all``: every feature in the layer becomes its own subcatchment.

Width and slope are synthesised when the source layer does not carry
them. The defaults match the smoke runs:

- ``width_m = sqrt(area_m2)`` (geometric proxy)
- ``slope_pct = 1.0`` (flat-by-default placeholder; replace once a
  DEM-based slope tool exists; F12 in BACKLOG).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import geopandas as gpd


MODES = ("by_id_field", "by_index", "largest", "all")


def _ensure_projected(gdf: gpd.GeoDataFrame, layer_label: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError(f"{layer_label} has no CRS; cannot compute area")
    if not gdf.crs.is_projected:
        raise ValueError(
            f"{layer_label} CRS {gdf.crs} is geographic; reproject to a projected CRS "
            "before computing subcatchment area / width."
        )
    return gdf


def _select(gdf: gpd.GeoDataFrame, mode: str, args: argparse.Namespace) -> gpd.GeoDataFrame:
    if mode == "by_id_field":
        if not args.id_field or args.id_value is None:
            raise ValueError("mode=by_id_field requires --id-field and --id-value")
        if args.id_field not in gdf.columns:
            raise ValueError(
                f"--id-field '{args.id_field}' not in basin layer columns: {list(gdf.columns)}"
            )
        # Match either as string or as the column's native dtype if it parses.
        col = gdf[args.id_field]
        match = gdf[col.astype(str) == str(args.id_value)]
        if len(match) == 0:
            raise ValueError(
                f"no basin matched {args.id_field}={args.id_value}; "
                f"sample values in field: {list(col.head(5).astype(str))}"
            )
        return match.reset_index(drop=True)
    if mode == "by_index":
        if args.index is None:
            raise ValueError("mode=by_index requires --index")
        if args.index < 0 or args.index >= len(gdf):
            raise ValueError(f"--index {args.index} out of range [0,{len(gdf) - 1}]")
        return gdf.iloc[[args.index]].reset_index(drop=True)
    if mode == "largest":
        gdf_sorted = gdf.copy()
        gdf_sorted["__area_m2__"] = gdf_sorted.geometry.area
        gdf_sorted = gdf_sorted.sort_values("__area_m2__", ascending=False)
        return gdf_sorted.iloc[[0]].drop(columns=["__area_m2__"]).reset_index(drop=True)
    if mode == "all":
        return gdf.reset_index(drop=True)
    raise ValueError(f"unknown mode: {mode}")


def _subcatchment_id(i: int, custom_prefix: str | None) -> str:
    prefix = custom_prefix if custom_prefix else "S"
    return f"{prefix}{i + 1}"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--basin-shp", required=True, help="Source basin shapefile or geojson path.")
    ap.add_argument("--mode", choices=MODES, default="by_id_field")
    ap.add_argument("--id-field", default="OBJECTID")
    ap.add_argument("--id-value", default=None)
    ap.add_argument("--index", type=int, default=None)
    ap.add_argument("--id-prefix", default="S", help="Prefix for generated subcatchment IDs.")
    ap.add_argument("--outlet-node-id", default="OUT1")
    ap.add_argument("--rain-gage-id", default="RG1")
    ap.add_argument("--default-slope-pct", type=float, default=1.0)
    ap.add_argument(
        "--width-method",
        choices=("sqrt_area",),
        default="sqrt_area",
        help="How to synthesise width when the source layer lacks one.",
    )
    ap.add_argument("--out-geojson", required=True)
    ap.add_argument("--out-csv", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    basin_path = Path(args.basin_shp)
    if not basin_path.exists():
        raise FileNotFoundError(basin_path)

    gdf = gpd.read_file(basin_path)
    if len(gdf) == 0:
        raise ValueError(f"basin layer is empty: {basin_path}")
    gdf = _ensure_projected(gdf, "basin")

    selected = _select(gdf, args.mode, args)
    if len(selected) == 0:
        raise ValueError("selection produced no features")

    rows: list[dict] = []
    out_features: list[dict] = []
    for i, (_, row) in enumerate(selected.iterrows()):
        sid = _subcatchment_id(i, args.id_prefix)
        geom = row.geometry
        area_m2 = float(geom.area)
        area_ha = area_m2 / 10000.0
        if args.width_method == "sqrt_area":
            width_m = float(area_m2) ** 0.5
        else:  # pragma: no cover  (only one supported value today)
            raise ValueError(f"unsupported width_method: {args.width_method}")
        rows.append({
            "subcatchment_id": sid,
            "outlet": args.outlet_node_id,
            "area_ha": area_ha,
            "width_m": width_m,
            "slope_pct": args.default_slope_pct,
            "rain_gage": args.rain_gage_id,
        })
        out_features.append({
            "type": "Feature",
            "properties": {"subcatchment_id": sid},
            "geometry": json.loads(gpd.GeoSeries([geom], crs=selected.crs).to_json())["features"][0]["geometry"],
        })

    out_geojson = Path(args.out_geojson)
    out_csv = Path(args.out_csv)
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    geojson_obj = {
        "type": "FeatureCollection",
        "name": "subcatchments",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{selected.crs.to_epsg()}"}}
        if selected.crs.to_epsg() else None,
        "features": out_features,
    }
    # Drop crs key if it ended up as None (some CRS don't have EPSG codes).
    geojson_obj = {k: v for k, v in geojson_obj.items() if v is not None}
    out_geojson.write_text(json.dumps(geojson_obj, indent=2), encoding="utf-8")

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["subcatchment_id", "outlet", "area_ha", "width_m", "slope_pct", "rain_gage"]
        )
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "ok": True,
        "skill": "swmm-gis",
        "tool": "basin_shp_to_subcatchments",
        "mode": args.mode,
        "counts": {
            "subcatchments_emitted": len(rows),
            "source_features_total": len(gdf),
        },
        "outlet_node_id": args.outlet_node_id,
        "rain_gage_id": args.rain_gage_id,
        "width_method": args.width_method,
        "default_slope_pct": args.default_slope_pct,
        "outputs": {
            "subcatchments_geojson": str(out_geojson),
            "subcatchments_csv": str(out_csv),
        },
        "inputs": {"basin_shp": str(basin_path)},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"basin_shp_to_subcatchments failed: {exc}", file=sys.stderr)
        raise
