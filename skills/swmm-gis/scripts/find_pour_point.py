#!/usr/bin/env python3
"""Find DEM-based pour point candidates for watershed outlet selection.

Methods:
- boundary_min_elev: choose the minimum elevation cell on the DEM boundary.
- boundary_max_accum: compute D8 flow accumulation (with depression fill + flat resolution),
  then choose the boundary cell with maximum accumulation.

Outputs:
- GeoJSON point (same CRS as DEM)
- Preview PNG (DEM + outlet marker)

This is intended as a reproducible preprocessing step for SWMM experiments.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import xy


def boundary_mask(shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    b = np.zeros((h, w), dtype=bool)
    b[0, :] = True
    b[-1, :] = True
    b[:, 0] = True
    b[:, -1] = True
    return b


def find_boundary_min_elev(dem: np.ma.MaskedArray) -> tuple[int, int, float]:
    b = boundary_mask(dem.shape)
    valid = b & (~dem.mask)
    vals = dem.data[valid]
    if vals.size == 0:
        raise RuntimeError("No valid border cells")
    minval = float(vals.min())
    idx = np.argwhere(valid & (dem.data == minval))[0]
    r, c = int(idx[0]), int(idx[1])
    return r, c, minval


def find_boundary_max_accum(dem_path: Path) -> tuple[int, int, float]:
    # Lazy import (heavier deps)
    from pysheds.grid import Grid

    grid = Grid.from_raster(str(dem_path))
    dem = grid.read_raster(str(dem_path))

    filled = grid.fill_depressions(dem)
    filled = grid.resolve_flats(filled)
    fdir = grid.flowdir(filled)
    acc = grid.accumulation(fdir)

    b = boundary_mask(acc.shape)
    valid = b & np.isfinite(acc)
    acc_border = np.where(valid, acc, -np.inf)
    r, c = np.unravel_index(np.argmax(acc_border), acc_border.shape)
    return int(r), int(c), float(acc_border[r, c])


def write_geojson_point(out_path: Path, x: float, y: float, props: dict):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [x, y]},
            }
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fc, indent=2), encoding="utf-8")


def plot_preview_png(dem_path: Path, x: float, y: float, out_png: Path, title: str | None = None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    with rasterio.open(dem_path) as ds:
        dem = ds.read(1, masked=True)
        extent = [ds.bounds.left, ds.bounds.right, ds.bounds.bottom, ds.bounds.top]
        crs = ds.crs

    vals = dem.compressed()
    vmin, vmax = np.quantile(vals, [0.02, 0.98])

    plt.rcParams.update({'font.family':'Arial','font.size':12})
    fig, ax = plt.subplots(figsize=(7, 5), dpi=200)
    im = ax.imshow(dem, extent=extent, origin='upper', cmap='terrain', vmin=vmin, vmax=vmax)
    ax.scatter([x], [y], c='red', s=50, marker='x', linewidths=2, label='Pour point')
    ax.set_xlabel('Easting (m)')
    ax.set_ylabel('Northing (m)')
    if title:
        ax.set_title(title)
    ax.legend(loc='lower left', framealpha=0.9)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label('Elevation')
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dem', required=True, type=Path)
    ap.add_argument('--method', required=True, choices=['boundary_min_elev', 'boundary_max_accum'])
    ap.add_argument('--out-geojson', required=True, type=Path)
    ap.add_argument('--out-png', required=True, type=Path)
    ap.add_argument('--name', default='pour_point')
    args = ap.parse_args()

    with rasterio.open(args.dem) as ds:
        dem = ds.read(1, masked=True)
        transform = ds.transform
        crs = ds.crs

    if args.method == 'boundary_min_elev':
        r, c, score = find_boundary_min_elev(dem)
        props = {"name": args.name, "method": args.method, "score": score}
    else:
        r, c, score = find_boundary_max_accum(args.dem)
        props = {"name": args.name, "method": args.method, "score": score}

    x, y = xy(transform, r, c, offset='center')
    props.update({"row": r, "col": c, "crs": crs.to_string() if crs else None})

    write_geojson_point(args.out_geojson, float(x), float(y), props)
    plot_preview_png(args.dem, float(x), float(y), args.out_png, title=None)

    print(json.dumps({"x": float(x), "y": float(y), **props}, indent=2))


if __name__ == '__main__':
    main()
