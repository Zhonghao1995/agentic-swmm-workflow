#!/usr/bin/env python3
"""Emit a minimal 1-junction + 1-outfall stub network.json.

Use when the basin has a known outlet point + name but no pipe-network
geometry yet. Drives the swmm-builder builder MCP for smoke runs and
real-data fallbacks without inventing a multi-pipe drainage system.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import shapefile


def _resolve_field_index(reader: shapefile.Reader, name: str) -> int:
    fields = [field[0] for field in reader.fields[1:]]
    if name not in fields:
        raise SystemExit(
            f"Field '{name}' not found in {reader.shp.name}. Available: {fields}"
        )
    return fields.index(name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shp", type=Path, required=True, help="Subcatchment shapefile carrying outlet attrs")
    ap.add_argument("--outlet-id-field", required=True)
    ap.add_argument("--outlet-x-field", required=True)
    ap.add_argument("--outlet-y-field", required=True)
    ap.add_argument("--invert-elev", type=float, required=True)
    ap.add_argument("--outfall-invert-elev", type=float, required=True)
    ap.add_argument("--outfall-offset-m", type=float, default=500.0)
    ap.add_argument("--conduit-roughness", type=float, default=0.013)
    ap.add_argument("--conduit-diameter", type=float, default=1.5)
    ap.add_argument("--outfall-id", default=None)
    ap.add_argument("--conduit-id", default="C_OUT")
    ap.add_argument("--max-depth", type=float, default=3.0)
    ap.add_argument("--flow-units", default="CMS")
    ap.add_argument("--out-json", type=Path, required=True)
    args = ap.parse_args()

    reader = shapefile.Reader(str(args.shp))
    idx_id = _resolve_field_index(reader, args.outlet_id_field)
    idx_x = _resolve_field_index(reader, args.outlet_x_field)
    idx_y = _resolve_field_index(reader, args.outlet_y_field)

    records = list(reader.records())
    if not records:
        raise SystemExit(f"No records in {args.shp}")
    first = records[0]
    outlet_id = str(first[idx_id]).strip()
    if not outlet_id:
        raise SystemExit(f"Blank outlet id in field '{args.outlet_id_field}' of {args.shp}")
    outlet_x = float(first[idx_x])
    outlet_y = float(first[idx_y])

    outfall_id = args.outfall_id or f"{outlet_id}_OUT"
    if outfall_id == outlet_id:
        raise SystemExit(f"--outfall-id must differ from junction id '{outlet_id}'")

    network = {
        "meta": {
            "name": f"{outlet_id}-minimal-stub",
            "flow_units": args.flow_units,
            "note": "Stub network produced by swmm-network/minimal_stub_network.py — single junction + free outfall.",
        },
        "junctions": [
            {
                "id": outlet_id,
                "invert_elev": args.invert_elev,
                "max_depth": args.max_depth,
                "init_depth": 0.0,
                "sur_depth": 0.0,
                "aponded": 0.0,
                "coordinates": {"x": outlet_x, "y": outlet_y},
            }
        ],
        "outfalls": [
            {
                "id": outfall_id,
                "invert_elev": args.outfall_invert_elev,
                "type": "FREE",
                "gated": False,
                "route_to": None,
                "coordinates": {"x": outlet_x + args.outfall_offset_m, "y": outlet_y},
            }
        ],
        "conduits": [
            {
                "id": args.conduit_id,
                "from_node": outlet_id,
                "to_node": outfall_id,
                "length": args.outfall_offset_m,
                "roughness": args.conduit_roughness,
                "in_offset": 0.0,
                "out_offset": 0.0,
                "init_flow": 0.0,
                "max_flow": None,
                "xsection": {
                    "shape": "CIRCULAR",
                    "geom1": args.conduit_diameter,
                    "geom2": 0.0,
                    "geom3": 0.0,
                    "geom4": 0.0,
                    "barrels": 1,
                },
            }
        ],
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(network, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out_json": str(args.out_json), "junction": outlet_id, "outfall": outfall_id}, indent=2))


if __name__ == "__main__":
    main()
