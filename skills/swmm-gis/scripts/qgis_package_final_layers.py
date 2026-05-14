#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import xy
from shapely.geometry import LineString, Point

from plot_qgis_standard_layers import plot_overview


SHAPEFILE_SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def copy_shapefile(src_shp: Path, dst_shp: Path) -> list[str]:
    if src_shp.suffix.lower() != ".shp":
        raise ValueError(f"Expected .shp source: {src_shp}")
    if not src_shp.exists():
        raise FileNotFoundError(src_shp)
    dst_shp.parent.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for suffix in SHAPEFILE_SIDECARS:
        src = src_shp.with_suffix(suffix)
        if src.exists():
            dst = dst_shp.with_suffix(suffix)
            shutil.copy2(src, dst)
            copied.append(str(dst))
    return copied


def copy_raster(src: Path, dst: Path) -> list[str]:
    if not src.exists():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied = [str(dst)]
    aux = src.with_name(src.name + ".aux.xml")
    if aux.exists():
        dst_aux = dst.with_name(dst.name + ".aux.xml")
        shutil.copy2(aux, dst_aux)
        copied.append(str(dst_aux))
    return copied


def clear_layer_stem(folder: Path, stem: str) -> None:
    for path in folder.glob(f"{stem}.*"):
        path.unlink()


def derive_flow_and_outfall(*, stream: Path, accumulation: Path, flow_shp: Path, outfall_shp: Path) -> dict[str, Any]:
    clear_layer_stem(flow_shp.parent, flow_shp.stem)
    clear_layer_stem(outfall_shp.parent, outfall_shp.stem)

    with rasterio.open(stream) as stream_ds, rasterio.open(accumulation) as acc_ds:
        stream_arr = stream_ds.read(1, masked=True)
        acc_arr = acc_ds.read(1, masked=True).astype(float)
        transform = stream_ds.transform
        crs = stream_ds.crs

        stream_mask = (~stream_arr.mask) & (stream_arr.filled(0) > 0)
        rows, cols = np.where(stream_mask)
        stream_cells = set(zip(rows.tolist(), cols.tolist()))

        geometries: list[LineString] = []
        records: list[dict[str, Any]] = []
        outfall_cell: tuple[int, int] | None = None
        outfall_acc = float("-inf")

        for r, c in zip(rows.tolist(), cols.tolist()):
            current_acc = float(acc_arr[r, c]) if not acc_arr.mask[r, c] else float("-inf")
            if current_acc > outfall_acc:
                outfall_acc = current_acc
                outfall_cell = (r, c)

            candidates: list[tuple[float, int, int]] = []
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if (nr, nc) in stream_cells and not acc_arr.mask[nr, nc]:
                        neighbor_acc = float(acc_arr[nr, nc])
                        if neighbor_acc > current_acc:
                            candidates.append((neighbor_acc, nr, nc))

            if not candidates:
                continue

            _, nr, nc = max(candidates)
            x1, y1 = xy(transform, r, c, offset="center")
            x2, y2 = xy(transform, nr, nc, offset="center")
            geometries.append(LineString([(x1, y1), (x2, y2)]))
            records.append(
                {
                    "from_row": int(r),
                    "from_col": int(c),
                    "to_row": int(nr),
                    "to_col": int(nc),
                    "acc_from": current_acc,
                    "acc_to": float(acc_arr[nr, nc]),
                }
            )

        flow = gpd.GeoDataFrame(records, geometry=geometries, crs=crs)
        flow.to_file(flow_shp)

        if outfall_cell is None:
            outfall = gpd.GeoDataFrame([{"role": "outfall", "accum": None}], geometry=[Point()], crs=crs)
        else:
            r, c = outfall_cell
            x, y = xy(transform, r, c, offset="center")
            outfall = gpd.GeoDataFrame(
                [{"role": "outfall", "row": int(r), "col": int(c), "accum": float(outfall_acc)}],
                geometry=[Point(x, y)],
                crs=crs,
            )
        outfall.to_file(outfall_shp)

    return {
        "flow_segments": int(len(records)),
        "outfalls": 1 if outfall_cell is not None else 0,
        "outfall_accumulation": None if outfall_cell is None else float(outfall_acc),
    }


def package_final_layers(args: argparse.Namespace) -> dict[str, Any]:
    final_dir: Path = args.final_dir
    final_dir.mkdir(parents=True, exist_ok=True)

    subcatchments_dst = final_dir / "subcatchments.shp"
    flow_dst = final_dir / "flow.shp"
    slope_dst = final_dir / "slope_percent.tif"
    outfall_dst = final_dir / "outfall.shp"
    overview_dst = final_dir / "overview.png"
    manifest_dst = final_dir / "manifest.json"

    for stem in ("subcatchments", "flow", "outfall"):
        clear_layer_stem(final_dir, stem)
    for path in (slope_dst, slope_dst.with_name(slope_dst.name + ".aux.xml"), overview_dst, manifest_dst):
        if path.exists():
            path.unlink()

    copied_subcatchments = copy_shapefile(args.subcatchments, subcatchments_dst)
    copied_slope = copy_raster(args.slope, slope_dst)
    flow_summary = derive_flow_and_outfall(
        stream=args.stream,
        accumulation=args.accumulation,
        flow_shp=flow_dst,
        outfall_shp=outfall_dst,
    )

    if not args.no_overview:
        plot_overview(
            slope=slope_dst,
            subcatchments=subcatchments_dst,
            flow=flow_dst,
            outfall=outfall_dst,
            out_png=overview_dst,
            title=args.title or "",
        )

    subcatchments = gpd.read_file(subcatchments_dst)
    manifest = {
        "ok": True,
        "case_id": args.case_id,
        "final_layers": {
            "subcatchments": str(subcatchments_dst),
            "flow": str(flow_dst),
            "slope": str(slope_dst),
            "outfall": str(outfall_dst),
            "overview": None if args.no_overview else str(overview_dst),
        },
        "sources": {
            "subcatchments": str(args.subcatchments),
            "stream": str(args.stream),
            "accumulation": str(args.accumulation),
            "slope": str(args.slope),
        },
        "counts": {
            "subcatchments": int(len(subcatchments)),
            **flow_summary,
        },
        "copied_files": {
            "subcatchments": copied_subcatchments,
            "slope": copied_slope,
        },
        "crs": str(subcatchments.crs) if subcatchments.crs else None,
        "note": "Audit artifacts remain in the parent run directories; this folder contains only user-facing GIS/SWMM layers.",
    }
    manifest_dst.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Package QGIS/GRASS watershed outputs into a clean final_layers folder.")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--subcatchments", type=Path, required=True)
    parser.add_argument("--stream", type=Path, required=True)
    parser.add_argument("--accumulation", type=Path, required=True)
    parser.add_argument("--slope", type=Path, required=True)
    parser.add_argument("--final-dir", type=Path, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--no-overview", action="store_true")
    args = parser.parse_args()
    print(json.dumps(package_final_layers(args), indent=2))


if __name__ == "__main__":
    main()
