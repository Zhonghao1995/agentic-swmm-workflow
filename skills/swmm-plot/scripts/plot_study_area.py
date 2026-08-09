#!/usr/bin/env python3
"""Render a study-area map from a SWMMCanada upstream bundle.

The classic report figure: WHERE the modeled area is and what it holds.
Composition (all layers ship inside the run's own upstream bundle, so
the figure is fully self-contained, no web tiles, no network access):

* DEM hillshade background (``dem_dtm.tif``),
* subcatchment polygons (``preview/network.geojson`` kind=subcatchment),
* conduit lines (kind=conduit),
* outfall markers (kind=outfall),
* scale bar, north arrow, and an annotation cartouche with the AOI's
  WGS84 extent, element counts, and CRS.

Inputs
------
``--run-dir`` locates ``10_upstream/swmmcanada/swmm_model.zip`` per the
canonical layout; ``--bundle`` points at a zip explicitly. Runs that
were not fetched from SWMMCanada have no bundle: the script exits 2
with a plain explanation instead of guessing.

Dependencies: geopandas + rasterio (the aiswmm ``gis`` extra). Absent
dependencies exit 2 with the install command.

Style follows the repository plot conventions (Arial, ticks inward, no
title); the cartouche carries the identifying text instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path


_NETWORK_MEMBER = "preview/network.geojson"
_DEM_MEMBER = "dem_dtm.tif"
_BUNDLE_RELPATH = Path("10_upstream/swmmcanada/swmm_model.zip")


def _fail(message: str) -> "SystemExit":
    print(f"error: {message}", file=sys.stderr)
    return SystemExit(2)


def _load_geo_stack():
    try:
        import geopandas as gpd
        import rasterio
        from rasterio.warp import (
            Resampling,
            calculate_default_transform,
            reproject,
        )
    except ImportError as exc:
        raise _fail(
            f"study-area map needs the gis extra ({exc.name} missing); "
            "install with: pip install 'aiswmm[gis]'"
        )
    return gpd, rasterio, Resampling, calculate_default_transform, reproject


def _resolve_bundle(args: argparse.Namespace) -> Path:
    if args.bundle:
        bundle = Path(args.bundle).expanduser()
        if not bundle.is_file():
            raise _fail(f"bundle not found: {bundle}")
        return bundle
    run_dir = Path(args.run_dir).expanduser()
    bundle = run_dir / _BUNDLE_RELPATH
    if not bundle.is_file():
        raise _fail(
            f"no SWMMCanada bundle at {bundle}. The study-area map is "
            "built from the upstream bundle's GIS layers, so it is "
            "available for runs fetched via the Canada service; for "
            "other runs pass --bundle pointing at a swmm_model.zip."
        )
    return bundle


def _extract_members(bundle: Path, dest: Path) -> tuple[Path, Path | None]:
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        if _NETWORK_MEMBER not in names:
            raise _fail(
                f"{bundle.name} has no {_NETWORK_MEMBER}; cannot draw the "
                "study area from this bundle."
            )
        zf.extract(_NETWORK_MEMBER, dest)
        dem_path: Path | None = None
        if _DEM_MEMBER in names:
            zf.extract(_DEM_MEMBER, dest)
            dem_path = dest / _DEM_MEMBER
    return dest / _NETWORK_MEMBER, dem_path


def _hillshade(dem_path: Path, dst_crs: str, rasterio, Resampling, calc, reproject):
    """Return (shade_array, extent) reprojected to ``dst_crs``."""
    import numpy as np
    from matplotlib.colors import LightSource

    with rasterio.open(dem_path) as src:
        transform, w, h = calc(src.crs, dst_crs, src.width, src.height, *src.bounds)
        dem = np.empty((h, w), dtype=np.float32)
        reproject(
            rasterio.band(src, 1),
            dem,
            dst_transform=transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
        left, top = transform.c, transform.f
        right = left + transform.a * w
        bottom = top + transform.e * h
    dem = np.where(dem < -1000, np.nan, dem)
    fill = float(np.nanmean(dem)) if np.isfinite(np.nanmean(dem)) else 0.0
    shade = LightSource(azdeg=315, altdeg=45).hillshade(
        np.nan_to_num(dem, nan=fill), vert_exag=2
    )
    return shade, (left, right, bottom, top)


def _scale_bar_length(width_m: float) -> int:
    """A round bar length near a fifth of the map width."""
    target = width_m / 5
    for candidate in (5000, 2000, 1000, 500, 200, 100, 50, 20, 10):
        if candidate <= target:
            return candidate
    return 10


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a study-area map from a SWMMCanada bundle."
    )
    parser.add_argument("--run-dir", type=Path, help="Run directory (canonical layout).")
    parser.add_argument("--bundle", type=Path, help="Explicit swmm_model.zip path.")
    parser.add_argument("--out-png", type=Path, default=None)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--place",
        default=None,
        help="Optional place label for the cartouche (e.g. 'James Bay, Victoria BC').",
    )
    args = parser.parse_args(argv)
    if not args.run_dir and not args.bundle:
        parser.error("pass --run-dir or --bundle")

    gpd, rasterio, Resampling, calc, reproject = _load_geo_stack()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import ScalarFormatter

    bundle = _resolve_bundle(args)
    with tempfile.TemporaryDirectory() as tmp:
        geojson_path, dem_path = _extract_members(bundle, Path(tmp))
        net = gpd.read_file(geojson_path)
        # Work in the local UTM zone so meters are meters.
        utm = net.estimate_utm_crs()
        net = net.to_crs(utm)
        subs = net[net["kind"] == "subcatchment"]
        links = net[net["kind"] == "conduit"]
        outfalls = net[net["kind"] == "outfall"]
        if subs.empty and links.empty:
            raise _fail("bundle network.geojson holds no subcatchments or conduits")

        shade = extent = None
        if dem_path is not None:
            try:
                shade, extent = _hillshade(
                    dem_path, str(utm), rasterio, Resampling, calc, reproject
                )
            except Exception as exc:  # DEM is decoration; never fatal.
                print(f"note: DEM hillshade skipped ({exc})", file=sys.stderr)

        plt.rcParams.update({"font.family": "Arial", "font.size": 12})
        fig, ax = plt.subplots(figsize=(7.2, 7.2))
        if shade is not None:
            ax.imshow(shade, cmap="gray", extent=extent, alpha=0.55, zorder=1)
        if not subs.empty:
            subs.plot(
                ax=ax,
                facecolor="#7db8da",
                edgecolor="#2f6a93",
                linewidth=0.35,
                alpha=0.35,
                zorder=2,
            )
        if not links.empty:
            links.plot(ax=ax, color="#12324a", linewidth=0.9, zorder=3)
        if not outfalls.empty:
            outfalls.plot(
                ax=ax, color="#c62828", marker="v", markersize=60, zorder=4
            )

        frame = subs if not subs.empty else links
        minx, miny, maxx, maxy = frame.total_bounds
        pad = max((maxx - minx), (maxy - miny)) * 0.04
        ax.set_xlim(minx - pad, maxx + pad)
        ax.set_ylim(miny - pad, maxy + pad)
        ax.set_aspect("equal")
        ax.tick_params(direction="in")
        fmt = ScalarFormatter(useOffset=False)
        fmt.set_scientific(False)
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
        zone = str(utm).split(":")[-1]
        ax.set_xlabel(f"Easting (m, EPSG:{zone})")
        ax.set_ylabel(f"Northing (m, EPSG:{zone})")

        bar = _scale_bar_length(maxx - minx)
        bx, by = minx + pad * 0.5, maxy - pad * 1.2
        ax.plot([bx, bx + bar], [by, by], color="k", lw=2.5, zorder=5)
        bar_label = f"{bar} m" if bar < 1000 else f"{bar // 1000} km"
        ax.text(bx + bar / 2, by + pad * 0.25, bar_label, ha="center", fontsize=10)
        ax.annotate(
            "N",
            xy=(0.955, 0.94),
            xytext=(0.955, 0.865),
            xycoords="axes fraction",
            ha="center",
            fontsize=13,
            fontweight="bold",
            arrowprops=dict(arrowstyle="-|>", color="k", lw=1.6),
        )

        wgs = frame.to_crs("EPSG:4326").total_bounds
        place = f"{args.place} | " if args.place else ""
        ax.text(
            0.012,
            0.012,
            f"{place}{len(subs)} subcatchments, {len(links)} conduits, "
            f"{len(outfalls)} outfalls\n"
            f"AOI {wgs[0]:.3f} to {wgs[2]:.3f} E, {wgs[1]:.3f} to {wgs[3]:.3f} N "
            f"(WGS84) | data: SWMMCanada bundle",
            transform=ax.transAxes,
            fontsize=8.5,
            va="bottom",
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none"),
            zorder=6,
        )

        out_png = args.out_png
        if out_png is None:
            if not args.run_dir:
                raise _fail("--out-png is required when only --bundle is given")
            out_png = Path(args.run_dir) / "08_plot" / "study_area.png"
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(out_png, dpi=args.dpi)
        print(json.dumps({
            "ok": True,
            "out_png": str(out_png),
            "subcatchments": int(len(subs)),
            "conduits": int(len(links)),
            "outfalls": int(len(outfalls)),
            "crs": str(utm),
            "dem_hillshade": shade is not None,
        }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
