#!/usr/bin/env python3
"""Render a SWMM model's spatial layout (network map) as a PNG.

Companion to ``plot_rain_runoff_si.py`` (which is the hydrograph view).
``aiswmm plot`` answers "what does the simulated flow look like over
time?". ``aiswmm map`` answers "what does the network look like in
space?" — the question every reviewer asks before they trust a SWMM
model. Both scripts share the same skill (``swmm-plot``) and the same
stylesheet (``plot_style.apply_style()``: the vendored Nature spec, 89 mm
single column, 7 pt sans-serif, ticks out, no grid, Wong palette, no
title) so a paper with both figures reads as one consistent diagnostic
pair. Output is the ``--out-png`` preview plus its vector twin
``<stem>.pdf`` (the submission file).

The data path is deliberately two-tier:

* **Preferred — SWMManywhere geoparquet artefacts.** When the run
  directory was produced by the swmm-anywhere chain (PRD
  swmmanywhere_integration), the runner copies
  ``nodes.geoparquet`` / ``edges.geoparquet`` /
  ``subcatchments.geoparquet`` under the canonical upstream box
  ``<run-dir>/10_upstream/swmmanywhere/`` (ADR-0004; older runs may carry
  it under the legacy flat ``<run-dir>/10_swmmanywhere/``).
  These carry the real WGS84 polygons SWMManywhere downloaded from OSM
  plus extra columns (``node_type``, ``outfall_id``) that drive the
  per-outfall colouring. We load them via ``geopandas`` *lazily* so the
  default aiswmm install (no ``[anywhere]`` extra) still has a working
  ``aiswmm map``.
* **Fallback — INP text parsing.** Every SWMM ``.inp`` file ships with
  ``[COORDINATES]`` (nodes), ``[VERTICES]`` (conduit shape points),
  ``[Polygons]`` (subcatchment boundaries), and ``[SUBCATCHMENTS]``
  (subcatchment→outlet linkage). Pure-text parsing of these four
  sections gives us everything the renderer needs without pulling in
  geopandas/pyarrow. This is the path that runs on a bare aiswmm
  install or against a hand-built INP that never touched
  SWMManywhere.

Colouring strategy: every conduit is traced upstream→downstream to its
terminal outfall via the ``[CONDUITS]`` adjacency. All conduits in the
same drainage area share that outfall's colour (the Wong palette's
categorical cycle), so a sub-network jumps out visually. Subcatchments
are tinted by the colour of the outfall their outlet drains to
(transitively). Junctions are small grey dots; outfalls are black ``★``
markers (the palette's first colour, kept out of the sub-network cycle)
so reviewers can find the model discharge points in one glance.

CLI surface (the driver — ``aiswmm map`` — forwards these):

    --inp <path>      explicit INP (overrides discovery)
    --run-dir <path>  run directory (used only to find the geoparquet trio)
    --out-png <path>  output PNG path (required); <stem>.pdf is written beside it
    --dpi <int>       resolution of the PNG preview (default 450; the PDF is vector)
    --no-subcatchments  skip the polygon layer
    --no-vertices       draw conduits as straight lines (ignore [VERTICES])
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

# Headless backend — same convention as plot_rain_runoff_si.py.
import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

# Shared style module next to this script (tests load the script through
# ``importlib.util.spec_from_file_location``, which does not put this
# directory on sys.path).
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from plot_style import (  # noqa: E402
    CATEGORY_CYCLE,
    WONG,
    apply_style,
    legend_outside,
    map_figsize,
    save_figure,
)


# --------------------------------------------------------------------- #
# INP text parsing (the fallback data source).
#
# Each helper consumes the full INP text once and returns the section
# it owns. We do NOT use a single multi-pass tokeniser — the script's
# value is that it works without any extra deps, so adding even a
# minimal class hierarchy would be over-engineered.
# --------------------------------------------------------------------- #


def _read_section_lines(inp_text: str, section: str) -> list[list[str]]:
    """Return a list of ``parts`` lists for every non-comment row in
    ``[section]``. ``parts`` is the result of ``.split()`` on the row,
    so callers index by position. Empty lists and comment lines are
    dropped.
    """
    rows: list[list[str]] = []
    in_section = False
    upper_section = f"[{section.upper()}]"
    for raw in inp_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper() == upper_section:
            in_section = True
            continue
        if in_section and line.startswith("[") and line.endswith("]"):
            break
        if not in_section:
            continue
        if line.startswith(";"):
            continue
        rows.append(line.split())
    return rows


def parse_inp_coordinates(inp_text: str) -> dict[str, tuple[float, float]]:
    """``[COORDINATES]`` -> {node_name: (x, y)}.

    SWMM stores ``;;Name X Y`` rows. We tolerate ``;`` and ``;;``
    comments and silently skip unparseable rows (some hand-edited INPs
    have ragged columns).
    """
    coords: dict[str, tuple[float, float]] = {}
    for parts in _read_section_lines(inp_text, "COORDINATES"):
        if len(parts) < 3:
            continue
        try:
            coords[parts[0]] = (float(parts[1]), float(parts[2]))
        except ValueError:
            continue
    return coords


def parse_inp_vertices(inp_text: str) -> dict[str, list[tuple[float, float]]]:
    """``[VERTICES]`` -> {conduit_name: [(x, y), ...]}.

    ``[VERTICES]`` is optional in SWMM; many INPs draw conduits as
    straight lines and skip the section entirely. Returns an empty
    dict in that case.
    """
    verts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for parts in _read_section_lines(inp_text, "VERTICES"):
        if len(parts) < 3:
            continue
        try:
            verts[parts[0]].append((float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return dict(verts)


def parse_inp_polygons(inp_text: str) -> dict[str, list[tuple[float, float]]]:
    """``[Polygons]`` (or ``[POLYGONS]``) -> {subcatchment_name: [(x, y), ...]}.

    SWMM rings the polygon implicitly (last vertex may or may not
    repeat the first). The renderer closes the ring itself, so we
    just collect rows in order.
    """
    polys: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for parts in _read_section_lines(inp_text, "POLYGONS"):
        if len(parts) < 3:
            continue
        try:
            polys[parts[0]].append((float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return dict(polys)


def parse_inp_subcatchments(inp_text: str) -> dict[str, str]:
    """``[SUBCATCHMENTS]`` -> {subcatchment_name: outlet_node_name}.

    Row layout is ``Name Raingage Outlet Area %Imperv Width %Slope ...``,
    so the outlet is column index 2.
    """
    out: dict[str, str] = {}
    for parts in _read_section_lines(inp_text, "SUBCATCHMENTS"):
        if len(parts) < 3:
            continue
        out[parts[0]] = parts[2]
    return out


def parse_inp_conduits(inp_text: str) -> list[tuple[str, str, str]]:
    """``[CONDUITS]`` -> [(name, from_node, to_node), ...]."""
    out: list[tuple[str, str, str]] = []
    for parts in _read_section_lines(inp_text, "CONDUITS"):
        if len(parts) < 3:
            continue
        out.append((parts[0], parts[1], parts[2]))
    return out


def parse_inp_node_kinds(inp_text: str) -> dict[str, str]:
    """Tag every node in ``[JUNCTIONS]`` / ``[OUTFALLS]`` / ``[STORAGE]``.

    Returns a {node_name: kind} mapping where ``kind`` is one of
    ``junction``, ``outfall``, ``storage``. Nodes only referenced
    from ``[COORDINATES]`` (no kind row) default to ``junction``
    in the renderer.
    """
    kinds: dict[str, str] = {}
    for parts in _read_section_lines(inp_text, "JUNCTIONS"):
        if parts:
            kinds[parts[0]] = "junction"
    for parts in _read_section_lines(inp_text, "OUTFALLS"):
        if parts:
            kinds[parts[0]] = "outfall"
    for parts in _read_section_lines(inp_text, "STORAGE"):
        if parts:
            kinds[parts[0]] = "storage"
    return kinds


# --------------------------------------------------------------------- #
# Outfall partitioning — turn (conduits, node_kinds) into a colouring.
#
# We BFS each outfall in reverse through the conduit graph: any node
# that can reach this outfall by following ``from_node -> to_node``
# downstream belongs to that outfall's sub-network. Conduits between
# two such nodes inherit the colour. Cycles (rare but legal in SWMM
# with pumps/orifices) are broken by the visited set.
# --------------------------------------------------------------------- #


def assign_outfall_colours(
    conduits: list[tuple[str, str, str]],
    node_kinds: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (node_outfall, conduit_outfall) keyed by name.

    ``node_outfall[n]`` = the outfall id ``n`` ultimately drains to
    (None when ``n`` is disconnected). ``conduit_outfall[c]`` =
    same, applied to the conduit's downstream node.
    """
    # Build reverse adjacency: who drains INTO this node?
    incoming: dict[str, list[str]] = defaultdict(list)
    for _, src, dst in conduits:
        incoming[dst].append(src)

    outfalls = sorted(n for n, k in node_kinds.items() if k == "outfall")
    node_outfall: dict[str, str] = {}
    # BFS upstream from each outfall.
    for o in outfalls:
        queue = deque([o])
        node_outfall.setdefault(o, o)
        while queue:
            cur = queue.popleft()
            for upstream in incoming.get(cur, []):
                if upstream in node_outfall:
                    continue  # first claim wins -> deterministic
                node_outfall[upstream] = o
                queue.append(upstream)

    conduit_outfall: dict[str, str] = {}
    for name, _src, dst in conduits:
        if dst in node_outfall:
            conduit_outfall[name] = node_outfall[dst]
    return node_outfall, conduit_outfall


def palette_for(outfalls: list[str]) -> dict[str, tuple[float, float, float]]:
    """Deterministic colour wheel for outfall sub-networks.

    Uses the Wong colour-blind-safe categorical cycle from ``plot_style``
    (black excluded: it marks the outfalls themselves), cycling when
    there are more outfalls than colours. The sorted outfall list drives
    the order so the same network always yields the same picture.
    """
    from matplotlib.colors import to_rgb

    out: dict[str, tuple[float, float, float]] = {}
    for i, o in enumerate(sorted(outfalls)):
        out[o] = to_rgb(CATEGORY_CYCLE[i % len(CATEGORY_CYCLE)])
    return out


# --------------------------------------------------------------------- #
# Geoparquet path (preferred when SWMManywhere chain ran).
#
# Lazy import: importing this script must not require geopandas.
# When geopandas is missing or the files are missing, the caller
# falls back to the INP path.
# --------------------------------------------------------------------- #


def try_load_geoparquet(synth_dir: Path) -> dict[str, Any] | None:
    """Return a dict of GeoDataFrames or ``None`` if unavailable.

    Returns ``None`` on any of: missing files, missing geopandas,
    missing pyarrow, or any read error. Callers fall back to the INP
    parsing path on ``None``.
    """
    needed = {
        "nodes": synth_dir / "nodes.geoparquet",
        "edges": synth_dir / "edges.geoparquet",
        "subcatchments": synth_dir / "subcatchments.geoparquet",
    }
    if not all(p.exists() for p in needed.values()):
        return None
    try:
        import geopandas as gpd  # type: ignore  # noqa: F401
    except ImportError:
        return None
    try:
        return {key: gpd.read_parquet(path) for key, path in needed.items()}
    except Exception:
        return None


# --------------------------------------------------------------------- #
# Rendering.
# --------------------------------------------------------------------- #


def _finish_map(fig, ax, out_png: Path, dpi: int) -> None:
    """Common tail: equal aspect, plain coordinates, legend above, PDF + PNG."""
    from matplotlib.ticker import MaxNLocator, ScalarFormatter

    ax.set_aspect("equal", adjustable="datalim")
    # Coordinates read as coordinates: no "x 10^6" offset pulled out of
    # the tick labels, and few enough ticks that 6-7 digit labels do not
    # touch at 89 mm (same choices as plot_study_area.py).
    for axis in (ax.xaxis, ax.yaxis):
        fmt = ScalarFormatter(useOffset=False)
        fmt.set_scientific(False)
        axis.set_major_formatter(fmt)
        axis.set_major_locator(MaxNLocator(nbins=5))
    handles, labels = ax.get_legend_handles_labels()
    if labels:
        legend_outside(fig, handles, labels)
    save_figure(fig, out_png, dpi=dpi)
    plt.close(fig)


_JUNCTION_GREY = "#555555"


def render_from_inp(
    *,
    inp_path: Path,
    out_png: Path,
    dpi: int,
    draw_subcatchments: bool,
    draw_vertices: bool,
) -> None:
    """Render the layout from a SWMM INP file alone.

    This is the dependency-light path — pure matplotlib, no
    geopandas. Always works as long as the INP carries at least
    ``[COORDINATES]``.
    """
    inp_text = inp_path.read_text(encoding="utf-8", errors="ignore")

    coords = parse_inp_coordinates(inp_text)
    if not coords:
        raise SystemExit(
            f"--inp has no [COORDINATES] section: {inp_path}\n"
            "Cannot render a layout from an INP without node coordinates."
        )
    polygons = parse_inp_polygons(inp_text) if draw_subcatchments else {}
    vertices = parse_inp_vertices(inp_text) if draw_vertices else {}
    sub_outlet = parse_inp_subcatchments(inp_text)
    conduits = parse_inp_conduits(inp_text)
    node_kinds = parse_inp_node_kinds(inp_text)

    node_outfall, conduit_outfall = assign_outfall_colours(conduits, node_kinds)
    outfalls = sorted(n for n, k in node_kinds.items() if k == "outfall")
    colours = palette_for(outfalls) if outfalls else {}

    apply_style()
    xs_all = [x for x, _ in coords.values()]
    ys_all = [y for _, y in coords.values()]
    fig, ax = plt.subplots(
        figsize=map_figsize((min(xs_all), min(ys_all), max(xs_all), max(ys_all))),
        layout="constrained",
    )

    # Layer 1: subcatchment polygons (light blue, optional). Tint by
    # the outlet's outfall colour when available so sub-networks are
    # visible even when the polygons hide most conduits.
    if draw_subcatchments and polygons:
        for sub_name, ring in polygons.items():
            if len(ring) < 3:
                continue
            outlet = sub_outlet.get(sub_name)
            tint = colours.get(node_outfall.get(outlet, ""), (0.6, 0.8, 0.95))
            patch = mpatches.Polygon(
                ring,
                closed=True,
                facecolor=(*tint, 0.18),
                edgecolor=(*tint, 0.6),
                linewidth=0.3,
                zorder=1,
            )
            ax.add_patch(patch)

    # Layer 2: conduits. Use [VERTICES] when present for the polyline
    # geometry; otherwise draw a straight line between end-node coords.
    fallback_colour = (0.4, 0.4, 0.4)
    for name, src, dst in conduits:
        if src not in coords or dst not in coords:
            continue
        xs = [coords[src][0]]
        ys = [coords[src][1]]
        if draw_vertices and name in vertices:
            for vx, vy in vertices[name]:
                xs.append(vx)
                ys.append(vy)
        xs.append(coords[dst][0])
        ys.append(coords[dst][1])
        colour = colours.get(conduit_outfall.get(name, ""), fallback_colour)
        ax.plot(xs, ys, color=colour, linewidth=0.6, zorder=3)

    # Layer 3: junctions (small grey dot) + storage (blue square).
    jx, jy, sx, sy = [], [], [], []
    for name, (x, y) in coords.items():
        kind = node_kinds.get(name, "junction")
        if kind == "outfall":
            continue  # drawn last
        if kind == "storage":
            sx.append(x)
            sy.append(y)
        else:
            jx.append(x)
            jy.append(y)
    if jx:
        ax.scatter(jx, jy, s=4, c=_JUNCTION_GREY, marker="o", linewidths=0, zorder=4, label="Junction")
    if sx:
        ax.scatter(sx, sy, s=9, c=WONG["blue"], marker="s", linewidths=0, zorder=4, label="Storage")

    # Layer 4: outfalls — drawn last so they stack on top.
    ox, oy = [], []
    for o in outfalls:
        if o not in coords:
            continue
        ox.append(coords[o][0])
        oy.append(coords[o][1])
    if ox:
        ax.scatter(
            ox,
            oy,
            s=40,
            c=WONG["black"],
            marker="*",
            edgecolor="white",
            linewidth=0.3,
            zorder=5,
            label="Outfall",
        )

    # Axis tidy-up. The INP might be in metres, feet, or projected XY;
    # we don't know, so the axis says so instead of guessing a unit.
    ax.set_xlabel("X (INP units)")
    ax.set_ylabel("Y (INP units)")
    _finish_map(fig, ax, out_png, dpi)


def render_from_geoparquet(
    *,
    gdfs: dict[str, Any],
    out_png: Path,
    dpi: int,
    draw_subcatchments: bool,
) -> None:
    """Render the layout from SWMManywhere geoparquet trio.

    Reached only when ``geopandas`` and the three files were
    available. The schema convention follows SWMManywhere v0.2.x:
    nodes carry a ``node_type`` column ("junction"/"outfall") and
    edges have ``LINESTRING`` geometries with their downstream node
    encoded in ``v`` (NetworkX-style ``(u, v)`` columns).
    """
    nodes = gdfs["nodes"]
    edges = gdfs["edges"]
    subs = gdfs["subcatchments"]

    # Build the per-outfall colouring on the geopandas frames. We
    # mirror the INP path's logic (BFS upstream from each outfall)
    # rather than relying on SWMManywhere-specific columns so a hand-
    # built geoparquet trio still renders.
    node_kinds: dict[str, str] = {}
    if "node_type" in nodes.columns:
        for _, row in nodes.iterrows():
            node_kinds[str(row["id"])] = str(row["node_type"]).lower()
    else:
        # No type column — treat every node as a junction; outfalls
        # surface from edges' end-of-line nodes that have no outgoing
        # edges. (Best effort; the INP path is the canonical surface.)
        downstream = set(str(v) for v in edges["v"]) if "v" in edges.columns else set()
        upstream = set(str(u) for u in edges["u"]) if "u" in edges.columns else set()
        for _, row in nodes.iterrows():
            nid = str(row["id"])
            if nid in downstream and nid not in upstream:
                node_kinds[nid] = "outfall"
            else:
                node_kinds[nid] = "junction"

    conduits = [
        (str(row.get("id", i)), str(row["u"]), str(row["v"]))
        for i, row in edges.iterrows()
        if "u" in edges.columns and "v" in edges.columns
    ]
    node_outfall, conduit_outfall = assign_outfall_colours(conduits, node_kinds)
    outfalls = sorted(n for n, k in node_kinds.items() if k == "outfall")
    colours = palette_for(outfalls) if outfalls else {}

    apply_style()
    frame = subs if (draw_subcatchments and not subs.empty) else edges
    fig, ax = plt.subplots(
        figsize=map_figsize(tuple(frame.total_bounds)),
        layout="constrained",
    )

    if draw_subcatchments and not subs.empty:
        if "outlet" in subs.columns:
            tint_for = lambda r: colours.get(  # noqa: E731 — tiny local helper
                node_outfall.get(str(r["outlet"]), ""), (0.6, 0.8, 0.95)
            )
        else:
            tint_for = lambda r: (0.6, 0.8, 0.95)  # noqa: E731
        for _, row in subs.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            tint = tint_for(row)
            polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
            for poly in polys:
                xs, ys = poly.exterior.xy
                ax.fill(xs, ys, color=(*tint, 0.18), edgecolor=(*tint, 0.6), linewidth=0.3, zorder=1)

    # Conduits via LINESTRING geometry; falls back to (u, v) end nodes
    # when geometry is missing.
    coord_lookup: dict[str, tuple[float, float]] = {}
    if "geometry" in nodes.columns:
        for _, row in nodes.iterrows():
            pt = row.geometry
            if pt is None or pt.is_empty:
                continue
            coord_lookup[str(row["id"])] = (pt.x, pt.y)

    fallback_colour = (0.4, 0.4, 0.4)
    for _, row in edges.iterrows():
        u, v = str(row.get("u", "")), str(row.get("v", ""))
        colour = colours.get(node_outfall.get(v, ""), fallback_colour)
        geom = row.geometry if "geometry" in edges.columns else None
        if geom is not None and not geom.is_empty:
            xs, ys = geom.xy
            ax.plot(xs, ys, color=colour, linewidth=0.6, zorder=3)
        elif u in coord_lookup and v in coord_lookup:
            ax.plot(
                [coord_lookup[u][0], coord_lookup[v][0]],
                [coord_lookup[u][1], coord_lookup[v][1]],
                color=colour,
                linewidth=0.6,
                zorder=3,
            )

    # Nodes
    jx, jy, ox, oy = [], [], [], []
    for nid, (x, y) in coord_lookup.items():
        if node_kinds.get(nid) == "outfall":
            ox.append(x)
            oy.append(y)
        else:
            jx.append(x)
            jy.append(y)
    if jx:
        ax.scatter(jx, jy, s=4, c=_JUNCTION_GREY, marker="o", linewidths=0, zorder=4, label="Junction")
    if ox:
        ax.scatter(
            ox,
            oy,
            s=40,
            c=WONG["black"],
            marker="*",
            edgecolor="white",
            linewidth=0.3,
            zorder=5,
            label="Outfall",
        )

    xlabel, ylabel = _crs_axis_labels(getattr(nodes, "crs", None))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    _finish_map(fig, ax, out_png, dpi)


def _crs_axis_labels(crs: Any) -> tuple[str, str]:
    """Axis labels with units, read off the geoparquet CRS when it has one."""
    try:
        if crs is None:
            return ("X (CRS units)", "Y (CRS units)")
        if crs.is_geographic:
            return ("Longitude (°)", "Latitude (°)")
        unit = crs.axis_info[0].unit_name if crs.axis_info else ""
        unit = {"metre": "m", "meter": "m", "foot": "ft", "US survey foot": "ft"}.get(unit, unit)
        return (f"Easting ({unit or 'CRS units'})", f"Northing ({unit or 'CRS units'})")
    except Exception:  # pragma: no cover - defensive against exotic CRS objects
        return ("X (CRS units)", "Y (CRS units)")


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Render a SWMM model's spatial layout (subcatchments, conduits, "
            "nodes, outfalls) as a PNG."
        )
    )
    p.add_argument(
        "--inp",
        type=Path,
        default=None,
        help=(
            "Path to a SWMM .inp file. Required when no SWMManywhere "
            "geoparquet trio is found under --synth-dir."
        ),
    )
    p.add_argument(
        "--synth-dir",
        type=Path,
        default=None,
        help=(
            "Optional path to a directory containing nodes.geoparquet, "
            "edges.geoparquet, subcatchments.geoparquet (the SWMManywhere "
            "chain output under <run-dir>/10_upstream/swmmanywhere/, or the "
            "legacy <run-dir>/10_swmmanywhere/ for older runs)."
        ),
    )
    p.add_argument(
        "--out-png",
        type=Path,
        required=True,
        help="Preview PNG path; the vector twin <stem>.pdf is written beside it.",
    )
    p.add_argument(
        "--dpi",
        type=int,
        default=450,
        help="Resolution of the preview PNG (the PDF is vector). Spec minimum for images is 450.",
    )
    p.add_argument(
        "--no-subcatchments",
        action="store_true",
        help="Skip the polygon (subcatchment) layer; render conduits + nodes only.",
    )
    p.add_argument(
        "--no-vertices",
        action="store_true",
        help=(
            "Draw conduits as straight lines between their end nodes, "
            "ignoring [VERTICES]. Useful when [VERTICES] is noisy or absent."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    draw_subcatchments = not args.no_subcatchments
    draw_vertices = not args.no_vertices

    # Try geoparquet first — only when the user pointed us at a synth_dir.
    if args.synth_dir is not None and args.synth_dir.exists():
        gdfs = try_load_geoparquet(args.synth_dir)
        if gdfs is not None:
            render_from_geoparquet(
                gdfs=gdfs,
                out_png=args.out_png,
                dpi=args.dpi,
                draw_subcatchments=draw_subcatchments,
            )
            print(f"wrote {args.out_png}")
            return 0

    # Fallback: INP parsing.
    if args.inp is None:
        print(
            "error: must provide --inp (or a --synth-dir containing the "
            "SWMManywhere geoparquet trio).",
            file=sys.stderr,
        )
        return 2
    if not args.inp.exists():
        print(f"error: --inp not found: {args.inp}", file=sys.stderr)
        return 1

    render_from_inp(
        inp_path=args.inp,
        out_png=args.out_png,
        dpi=args.dpi,
        draw_subcatchments=draw_subcatchments,
        draw_vertices=draw_vertices,
    )
    print(f"wrote {args.out_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
