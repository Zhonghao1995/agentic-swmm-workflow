#!/usr/bin/env python3
"""Convert a SWMM-attributed subcatchment shapefile into builder-ready CSV.

Many real-world basins (e.g. Saanich Boundary.shp) already carry SWMM
subcatchment attributes (area, width, slope, outlet, rain gage). This helper
bridges those shapefiles into the `swmm-builder` CSV contract without forcing
the user to rerun GIS preprocessing.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import shapefile


def _resolve_field_index(reader: shapefile.Reader, name: str) -> int:
    fields = [field[0] for field in reader.fields[1:]]
    if name not in fields:
        raise SystemExit(
            f"Field '{name}' not found in {reader.shp.name}. "
            f"Available: {fields}"
        )
    return fields.index(name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shp", type=Path, required=True)
    ap.add_argument("--id-field", required=True)
    ap.add_argument("--outlet-field", required=True)
    ap.add_argument("--area-ha-field", required=True)
    ap.add_argument("--width-m-field", required=True)
    ap.add_argument("--slope-pct-field", required=True)
    ap.add_argument("--curb-length-m-field", default=None)
    ap.add_argument("--rain-gage-field", default=None)
    ap.add_argument("--default-rain-gage", default=None)
    ap.add_argument("--out-csv", type=Path, required=True)
    args = ap.parse_args()

    reader = shapefile.Reader(str(args.shp))
    idx_id = _resolve_field_index(reader, args.id_field)
    idx_outlet = _resolve_field_index(reader, args.outlet_field)
    idx_area = _resolve_field_index(reader, args.area_ha_field)
    idx_width = _resolve_field_index(reader, args.width_m_field)
    idx_slope = _resolve_field_index(reader, args.slope_pct_field)
    idx_curb = (
        _resolve_field_index(reader, args.curb_length_m_field)
        if args.curb_length_m_field
        else None
    )
    idx_rain_gage = (
        _resolve_field_index(reader, args.rain_gage_field)
        if args.rain_gage_field
        else None
    )

    rows = []
    for record in reader.records():
        sub_id = str(record[idx_id]).strip()
        if not sub_id:
            raise SystemExit(f"Blank id in field '{args.id_field}' of {args.shp}")
        outlet = str(record[idx_outlet]).strip()
        if not outlet:
            raise SystemExit(
                f"Blank outlet in field '{args.outlet_field}' for record id '{sub_id}' in {args.shp}"
            )
        area_ha = float(record[idx_area])
        width_m = float(record[idx_width])
        slope_pct = float(record[idx_slope])
        curb_length_m = float(record[idx_curb]) if idx_curb is not None else 0.0

        if idx_rain_gage is not None:
            rain_gage = str(record[idx_rain_gage]).strip()
        elif args.default_rain_gage:
            rain_gage = args.default_rain_gage
        else:
            rain_gage = ""

        rows.append({
            "subcatchment_id": sub_id,
            "outlet": outlet,
            "area_ha": f"{area_ha:.6f}",
            "width_m": f"{width_m:.3f}",
            "slope_pct": f"{slope_pct:.3f}",
            "rain_gage": rain_gage,
            "curb_length_m": f"{curb_length_m:.3f}",
        })

    if not rows:
        raise SystemExit(f"No records found in {args.shp}")

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    headers = ["subcatchment_id", "outlet", "area_ha", "width_m", "slope_pct", "rain_gage", "curb_length_m"]
    with args.out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    print(
        '{"ok": true, "out_csv": "%s", "rows": %d}' % (args.out_csv, len(rows))
    )


if __name__ == "__main__":
    main()
