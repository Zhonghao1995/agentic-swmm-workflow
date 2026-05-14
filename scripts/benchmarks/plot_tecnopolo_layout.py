#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_section(path: Path, section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    target = f"[{section.upper()}]"
    reading = False
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper == target:
            reading = True
            continue
        if reading and line.startswith("[") and line.endswith("]"):
            break
        if not reading or not line or line.startswith(";"):
            continue
        rows.append(line.split())
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot the Tecnopolo prepared-input SWMM model layout.")
    parser.add_argument("--inp", required=True, type=Path)
    parser.add_argument("--out-png", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    plt.rcParams.update({
        "font.family": "Arial",
        "font.size": 9,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
    })

    coords = {row[0]: (float(row[1]), float(row[2])) for row in read_section(args.inp, "COORDINATES") if len(row) >= 3}
    conduits = [(row[0], row[1], row[2]) for row in read_section(args.inp, "CONDUITS") if len(row) >= 3]
    subcatchments = [(row[0], row[2], float(row[3])) for row in read_section(args.inp, "SUBCATCHMENTS") if len(row) >= 4]
    symbols = {row[0]: (float(row[1]), float(row[2])) for row in read_section(args.inp, "SYMBOLS") if len(row) >= 3}

    routed_count = Counter(outlet for _, outlet, _ in subcatchments)
    routed_area = defaultdict(float)
    for _, outlet, area in subcatchments:
        routed_area[outlet] += area

    fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=args.dpi)
    ax.set_facecolor("#F8FAFC")

    for _, from_node, to_node in conduits:
        if from_node not in coords or to_node not in coords:
            continue
        x0, y0 = coords[from_node]
        x1, y1 = coords[to_node]
        ax.plot([x0, x1], [y0, y1], color="#6B7280", linewidth=1.4, alpha=0.72, zorder=1)

    junctions = {name: xy for name, xy in coords.items() if not name.startswith("OUT") and not name.startswith("OU")}
    outfalls = {name: xy for name, xy in coords.items() if name.startswith("OUT") or name.startswith("OU")}
    if junctions:
        xs, ys = zip(*junctions.values())
        ax.scatter(xs, ys, s=28, color="#1F2937", edgecolor="white", linewidth=0.5, label="Junctions", zorder=3)
    if outfalls:
        xs, ys = zip(*outfalls.values())
        ax.scatter(xs, ys, s=70, marker="s", color="#B91C1C", edgecolor="white", linewidth=0.6, label="Outfalls", zorder=4)

    bubble_x = []
    bubble_y = []
    bubble_size = []
    for outlet, count in routed_count.items():
        if outlet not in coords:
            continue
        x, y = coords[outlet]
        bubble_x.append(x)
        bubble_y.append(y)
        bubble_size.append(70 + 95 * count)
    if bubble_x:
        ax.scatter(
            bubble_x,
            bubble_y,
            s=bubble_size,
            facecolor="#14B8A6",
            edgecolor="#0F766E",
            linewidth=0.8,
            alpha=0.28,
            label="Subcatchment outlets",
            zorder=2,
        )

    for gage, (x, y) in symbols.items():
        ax.scatter([x], [y], s=95, marker="D", color="#2563EB", edgecolor="white", linewidth=0.7, label="Rain gage", zorder=5)
        ax.annotate(gage, (x, y), xytext=(7, 7), textcoords="offset points", fontsize=9, color="#1E3A8A")

    for name in ["OUT_0", "OU2", "J22"]:
        if name in coords:
            ax.annotate(name, coords[name], xytext=(6, -12), textcoords="offset points", fontsize=9, color="#111827")

    total_area = sum(area for _, _, area in subcatchments)
    ax.text(
        0.015,
        0.985,
        f"Prepared INP layout\n{len(subcatchments)} subcatchments, {len(junctions)} junctions, {len(conduits)} conduits\nTotal subcatchment area: {total_area:.2f} ha",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.5,
        color="#111827",
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": "#D1D5DB", "alpha": 0.86},
    )

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("SWMM map X coordinate")
    ax.set_ylabel("SWMM map Y coordinate")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(loc="lower right", frameon=True, framealpha=0.88, borderpad=0.55, handlelength=1.4, markerscale=0.8)
    fig.tight_layout()

    args.out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out_png, dpi=args.dpi)


if __name__ == "__main__":
    main()
