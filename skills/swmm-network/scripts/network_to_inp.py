#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def format_num(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "YES" if x else "NO"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return str(x)


def emit_junctions(network: dict) -> list[str]:
    lines = ["[JUNCTIONS]", ";;Name           Elevation      MaxDepth       InitDepth      SurDepth       Aponded"]
    for j in network.get("junctions", []):
        lines.append(
            f"{j['id']:<16} {format_num(j['invert_elev']):<14} {format_num(j['max_depth']):<14} {format_num(j.get('init_depth', 0)):<14} {format_num(j.get('sur_depth', 0)):<14} {format_num(j.get('aponded', 0))}"
        )
    return lines


def emit_outfalls(network: dict) -> list[str]:
    lines = ["[OUTFALLS]", ";;Name           Elevation      Type           Stage Data      Gated          Route To"]
    for o in network.get("outfalls", []):
        lines.append(
            f"{o['id']:<16} {format_num(o['invert_elev']):<14} {o['type']:<14} {format_num(o.get('stage_data', '')):<15} {format_num(o.get('gated', False)):<14} {format_num(o.get('route_to', ''))}"
        )
    return lines


def emit_conduits(network: dict) -> list[str]:
    lines = ["[CONDUITS]", ";;Name           From Node       To Node         Length         Roughness      InOffset       OutOffset      InitFlow       MaxFlow"]
    for c in network.get("conduits", []):
        lines.append(
            f"{c['id']:<16} {c['from_node']:<15} {c['to_node']:<15} {format_num(c['length']):<14} {format_num(c['roughness']):<14} {format_num(c.get('in_offset', 0)):<14} {format_num(c.get('out_offset', 0)):<14} {format_num(c.get('init_flow', 0)):<14} {format_num(c.get('max_flow', ''))}"
        )
    return lines


def emit_xsections(network: dict) -> list[str]:
    lines = ["[XSECTIONS]", ";;Link           Shape           Geom1          Geom2          Geom3          Geom4          Barrels"]
    for c in network.get("conduits", []):
        xs = c["xsection"]
        lines.append(
            f"{c['id']:<16} {xs['shape']:<15} {format_num(xs['geom1']):<14} {format_num(xs.get('geom2', 0)):<14} {format_num(xs.get('geom3', 0)):<14} {format_num(xs.get('geom4', 0)):<14} {format_num(xs.get('barrels', 1))}"
        )
    return lines


def emit_coordinates(network: dict) -> list[str]:
    lines = ["[COORDINATES]", ";;Node           X-Coord         Y-Coord"]
    for j in network.get("junctions", []):
        xy = j["coordinates"]
        lines.append(f"{j['id']:<16} {format_num(xy['x']):<15} {format_num(xy['y'])}")
    for o in network.get("outfalls", []):
        xy = o["coordinates"]
        lines.append(f"{o['id']:<16} {format_num(xy['x']):<15} {format_num(xy['y'])}")
    return lines


def emit_vertices(network: dict) -> list[str]:
    lines = ["[VERTICES]", ";;Link           X-Coord         Y-Coord"]
    found = False
    for c in network.get("conduits", []):
        for v in c.get("vertices", []) or []:
            found = True
            lines.append(f"{c['id']:<16} {format_num(v['x']):<15} {format_num(v['y'])}")
    return lines if found else []


def render_inp(network: dict) -> str:
    blocks = [
        emit_junctions(network),
        emit_outfalls(network),
        emit_conduits(network),
        emit_xsections(network),
        emit_coordinates(network),
    ]
    vertices = emit_vertices(network)
    if vertices:
        blocks.append(vertices)
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("network_json", type=Path)
    ap.add_argument("--out", default=None, type=Path)
    args = ap.parse_args()

    network = load_json(args.network_json)
    text = render_inp(network)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
