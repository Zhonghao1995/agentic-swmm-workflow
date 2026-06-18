#!/usr/bin/env python3
"""Convert a SWMM ``.inp`` into the swmm-network ``network.json`` shape.

This is the input bridge that lets ``network_qa.py`` run its structural
checks (isolated_node / no_outfall_path / counts / xsection validity) on
ANY model that emitted an ``.inp`` — including a SWMManywhere-synthesized
network, which produces an ``.inp`` but no ``network.json``. With this,
the same structural QA serves the real-data paths (which already build a
``network.json``) and the synth path uniformly.

Standalone: stdlib only, no ``agentic_swmm`` / sibling-skill imports, so it
runs identically whether invoked by the MCP server, the CLI, or a test.

Only the sections the QA needs are parsed: ``[COORDINATES]``, ``[JUNCTIONS]``,
``[OUTFALLS]``, ``[CONDUITS]``, ``[XSECTIONS]``. Other sections are ignored.
A conduit with no matching ``[XSECTIONS]`` row is emitted without an
``xsection`` key on purpose — that is exactly what network_qa flags as
``missing_xsection``, so the gap stays visible rather than being papered over.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SECTION_RE = re.compile(r"^\s*\[([A-Z_]+)\]\s*$")


def parse_inp_sections(text: str) -> dict[str, list[str]]:
    """Split INP text into ``{SECTION: [non-comment data rows]}``."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = _SECTION_RE.match(raw)
        if m:
            current = m.group(1).upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(stripped)
    return sections


def inp_to_network(inp_path: str | Path) -> dict[str, Any]:
    """Build the ``network.json`` dict that ``network_qa.run_qa`` consumes."""
    text = Path(inp_path).read_text(encoding="utf-8", errors="replace")
    sections = parse_inp_sections(text)

    # [COORDINATES]: Node Xcoord Ycoord
    coords: dict[str, dict[str, float]] = {}
    for row in sections.get("COORDINATES", []):
        cols = row.split()
        if len(cols) >= 3:
            try:
                coords[cols[0]] = {"x": float(cols[1]), "y": float(cols[2])}
            except ValueError:
                continue

    def _node(name: str) -> dict[str, Any]:
        node: dict[str, Any] = {"id": name}
        if name in coords:
            node["coordinates"] = coords[name]
        return node

    junctions = [_node(r.split()[0]) for r in sections.get("JUNCTIONS", []) if r.split()]
    outfalls = [_node(r.split()[0]) for r in sections.get("OUTFALLS", []) if r.split()]

    # [XSECTIONS]: Link Shape Geom1 ...
    xsections: dict[str, dict[str, Any]] = {}
    for row in sections.get("XSECTIONS", []):
        cols = row.split()
        if len(cols) >= 3:
            try:
                xsections[cols[0]] = {"shape": cols[1].upper(), "geom1": float(cols[2])}
            except ValueError:
                continue

    # [CONDUITS]: Name FromNode ToNode Length Roughness ...
    conduits: list[dict[str, Any]] = []
    for row in sections.get("CONDUITS", []):
        cols = row.split()
        if len(cols) < 5:
            continue
        try:
            conduit: dict[str, Any] = {
                "id": cols[0],
                "from_node": cols[1],
                "to_node": cols[2],
                "length": float(cols[3]),
                "roughness": float(cols[4]),
            }
        except ValueError:
            continue
        if cols[0] in xsections:
            conduit["xsection"] = xsections[cols[0]]
        conduits.append(conduit)

    return {
        "junctions": junctions,
        "outfalls": outfalls,
        "conduits": conduits,
        "meta": {"source": "inp", "inp_path": str(inp_path)},
    }
