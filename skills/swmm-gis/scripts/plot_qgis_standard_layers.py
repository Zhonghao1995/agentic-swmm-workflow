#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.lines import Line2D
from pyproj import Transformer


def raster_extent(dataset: rasterio.DatasetReader) -> tuple[float, float, float, float]:
    bounds = dataset.bounds
    return bounds.left, bounds.right, bounds.bottom, bounds.top


def add_lonlat_ticks(ax: plt.Axes, crs: str, extent: tuple[float, float, float, float]) -> None:
    left, right, bottom, top = extent
    xs = np.linspace(left, right, 5)
    ys = np.linspace(bottom, top, 5)
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon_labels = [transformer.transform(float(x), bottom)[0] for x in xs]
    lat_labels = [transformer.transform(left, float(y))[1] for y in ys]
    ax.set_xticks(xs)
    ax.set_yticks(ys)
    ax.set_xticklabels([f"{lon:.3f}°" for lon in lon_labels], fontsize=11)
    ax.set_yticklabels([f"{lat:.3f}°" for lat in lat_labels], fontsize=11)
    ax.tick_params(top=True, right=True, labeltop=False, labelright=False, direction="in", length=5, width=1.0)
    ax.set_xlabel("Longitude", fontsize=12)
    ax.set_ylabel("Latitude", fontsize=12)


def plot_overview(
    *,
    slope: Path,
    subcatchments: Path,
    flow: Path,
    outfall: Path,
    out_png: Path,
    title: str,
) -> None:
    with rasterio.open(slope) as ds:
        arr = ds.read(1, masked=True).astype(float)
        extent = raster_extent(ds)
        crs = ds.crs.to_string()

    data = arr.compressed()
    vmax = float(np.percentile(data, 98)) if data.size else 1.0
    vmin = float(np.percentile(data, 2)) if data.size else 0.0

    sub = gpd.read_file(subcatchments)
    flow_gdf = gpd.read_file(flow)
    outfall_gdf = gpd.read_file(outfall)
    if sub.crs and sub.crs.to_string() != crs:
        sub = sub.to_crs(crs)
    if flow_gdf.crs and flow_gdf.crs.to_string() != crs:
        flow_gdf = flow_gdf.to_crs(crs)
    if outfall_gdf.crs and outfall_gdf.crs.to_string() != crs:
        outfall_gdf = outfall_gdf.to_crs(crs)

    plt.rcParams.update(
        {
            "font.family": ["Arial", "Helvetica", "DejaVu Sans"],
            "axes.linewidth": 0.8,
            "figure.dpi": 180,
            "savefig.dpi": 240,
        }
    )
    fig, ax = plt.subplots(figsize=(7.2, 9.0), constrained_layout=True)
    image = ax.imshow(
        arr,
        extent=extent,
        origin="upper",
        cmap="RdYlGn_r",
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
        alpha=0.62,
    )
    sub.boundary.plot(ax=ax, color="#343a40", linewidth=1.15, alpha=0.96)
    if not flow_gdf.empty:
        flow_gdf.plot(ax=ax, color="#0072B2", linewidth=1.85, alpha=0.96)
    if not outfall_gdf.empty:
        outfall_gdf.plot(ax=ax, marker="*", color="#D55E00", edgecolor="white", linewidth=0.6, markersize=130)

    if title:
        ax.set_title(title, fontsize=14, pad=8)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal")
    add_lonlat_ticks(ax, crs, extent)
    ax.grid(color="#b8bec6", linewidth=0.35, alpha=0.6)

    cbar = fig.colorbar(image, ax=ax, fraction=0.034, pad=0.018)
    cbar.set_label("Slope (%)", fontsize=12)
    cbar.ax.tick_params(labelsize=11)

    handles = [
        Line2D([0], [0], color="#343a40", linewidth=2.1, label="Subcatchment boundary"),
        Line2D([0], [0], color="#0072B2", linewidth=3.0, label="Flow path"),
        Line2D([0], [0], marker="*", color="none", markerfacecolor="#D55E00", markeredgecolor="white", markersize=13, label="Outfall"),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, framealpha=0.94, fontsize=11)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot clean QGIS/GRASS standard watershed final layers.")
    parser.add_argument("--slope", type=Path, required=True)
    parser.add_argument("--subcatchments", type=Path, required=True)
    parser.add_argument("--flow", type=Path, required=True)
    parser.add_argument("--outfall", type=Path, required=True)
    parser.add_argument("--out-png", type=Path, required=True)
    parser.add_argument("--title", default="")
    args = parser.parse_args()
    plot_overview(
        slope=args.slope,
        subcatchments=args.subcatchments,
        flow=args.flow,
        outfall=args.outfall,
        out_png=args.out_png,
        title=args.title,
    )


if __name__ == "__main__":
    main()
