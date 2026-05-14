#!/usr/bin/env python3
"""Clip raw municipal storm shapefiles to a basin and emit adapter-ready GeoJSON.

Bridges raw `StormGravityMain.shp` (+ optional `StormManhole.shp`) into the
GeoJSON inputs that `swmm-network-mcp.import_city_network` expects, while
also filling a `mapping.json` from a template.

This tool DOES NOT pick the outfall (see `infer_outfall.py`) and DOES NOT
reorient pipes by flow direction (see `reorient_pipes.py`). It performs
clip + reproject-check + template fill only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import shape


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_basin_polygon(path: Path) -> tuple[gpd.GeoDataFrame, "CRS"]:
    gdf = gpd.read_file(path)
    if len(gdf) == 0:
        raise ValueError(f"basin clip is empty: {path}")
    if gdf.crs is None:
        raise ValueError(f"basin clip has no CRS: {path}")
    return gdf, gdf.crs


def clip_to_basin(
    layer_path: Path,
    basin_gdf: gpd.GeoDataFrame,
    basin_crs,
    layer_label: str,
) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(layer_path)
    if gdf.crs is None:
        raise ValueError(f"{layer_label} has no CRS: {layer_path}")
    if gdf.crs != basin_crs:
        gdf = gdf.to_crs(basin_crs)
    clipped = gpd.clip(gdf, basin_gdf)
    # gpd.clip can leave empty geometries when nothing intersects; drop them
    clipped = clipped[~clipped.geometry.is_empty & clipped.geometry.notna()].copy()
    clipped.reset_index(drop=True, inplace=True)
    return clipped


def fill_mapping_template(
    template_path: Path,
    case_name: str,
    source_description: str,
    diameter_policy: str | None,
) -> dict:
    mapping = json.loads(template_path.read_text(encoding="utf-8"))
    meta = mapping.setdefault("meta", {})
    meta["name"] = case_name
    meta["source"] = source_description
    if diameter_policy is not None:
        meta["diameter_policy"] = diameter_policy
    return mapping


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipes-shp", required=True, help="Path to storm pipe LineString shapefile.")
    ap.add_argument("--manholes-shp", default=None, help="Optional Point shapefile of manholes / junctions.")
    ap.add_argument("--basin-clip", required=True, help="GeoJSON polygon defining the basin to clip to.")
    ap.add_argument("--mapping-template", required=True, help="city_mapping_raw_shapefile.template.json path.")
    ap.add_argument("--out-dir", required=True, help="Output directory; will be created if missing.")
    ap.add_argument("--case-name", required=True, help="Value to write into meta.name of mapping.json.")
    ap.add_argument(
        "--source-description",
        required=True,
        help="Value to write into meta.source of mapping.json (e.g. 'Saanich StormWaterSHP subset').",
    )
    ap.add_argument(
        "--diameter-policy",
        default=None,
        help="Optional value for meta.diameter_policy in mapping.json.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    pipes_path = Path(args.pipes_shp)
    basin_path = Path(args.basin_clip)
    template_path = Path(args.mapping_template)
    out_dir = Path(args.out_dir)
    manholes_path = Path(args.manholes_shp) if args.manholes_shp else None

    for required in (pipes_path, basin_path, template_path):
        if not required.exists():
            raise FileNotFoundError(required)
    if manholes_path is not None and not manholes_path.exists():
        raise FileNotFoundError(manholes_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    pipes_out = out_dir / "pipes.geojson"
    manholes_out = out_dir / "manholes.geojson" if manholes_path else None
    mapping_out = out_dir / "mapping.json"

    basin_gdf, basin_crs = load_basin_polygon(basin_path)

    pipes_clipped = clip_to_basin(pipes_path, basin_gdf, basin_crs, "pipes")
    if len(pipes_clipped) == 0:
        raise ValueError(
            f"no pipe features intersect basin {basin_path} (clipped count = 0). "
            "Check CRS consistency and that the basin polygon overlaps the pipe layer."
        )
    pipes_clipped.to_file(pipes_out, driver="GeoJSON")

    manholes_count: int | None = None
    if manholes_path is not None and manholes_out is not None:
        manholes_clipped = clip_to_basin(manholes_path, basin_gdf, basin_crs, "manholes")
        manholes_count = len(manholes_clipped)
        manholes_clipped.to_file(manholes_out, driver="GeoJSON")

    mapping = fill_mapping_template(
        template_path,
        case_name=args.case_name,
        source_description=args.source_description,
        diameter_policy=args.diameter_policy,
    )
    mapping_out.write_text(json.dumps(mapping, indent=2), encoding="utf-8")

    summary = {
        "ok": True,
        "skill": "swmm-network",
        "tool": "prepare_storm_inputs",
        "case_name": args.case_name,
        "basin_crs": str(basin_crs),
        "counts": {
            "pipes_clipped": len(pipes_clipped),
            "manholes_clipped": manholes_count,
        },
        "outputs": {
            "pipes_geojson": str(pipes_out),
            "manholes_geojson": str(manholes_out) if manholes_out else None,
            "mapping_json": str(mapping_out),
        },
        "inputs": {
            "pipes_shp": str(pipes_path),
            "manholes_shp": str(manholes_path) if manholes_path else None,
            "basin_clip": str(basin_path),
            "mapping_template": str(template_path),
        },
        "input_hashes": {
            "basin_clip_sha256": sha256_file(basin_path),
            "mapping_template_sha256": sha256_file(template_path),
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"prepare_storm_inputs failed: {exc}", file=sys.stderr)
        raise
