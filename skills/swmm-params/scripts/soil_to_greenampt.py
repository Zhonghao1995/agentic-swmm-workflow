#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOOKUP = SKILL_DIR / "references/soil_texture_to_greenampt.csv"


def normalize_key(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")
    return rows


def parse_float(value: Any, *, field: str, csv_path: Path, row_number: int) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing numeric value for '{field}' at {csv_path}:{row_number}")
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid float for '{field}' at {csv_path}:{row_number}: {value}") from exc


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def build_lookup(lookup_rows: list[dict[str, str]], lookup_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    lookup: dict[str, dict[str, Any]] = {}
    default_record: dict[str, Any] | None = None
    for i, row in enumerate(lookup_rows, start=2):
        raw_key = (row.get("texture") or "").strip()
        if not raw_key:
            raise ValueError(f"Missing 'texture' in lookup at {lookup_path}:{i}")
        key = normalize_key(raw_key)
        if key in lookup:
            raise ValueError(f"Duplicate lookup key '{raw_key}' in {lookup_path}")
        rec = {
            "texture": raw_key,
            "suction_mm": parse_float(row.get("suction_mm"), field="suction_mm", csv_path=lookup_path, row_number=i),
            "ksat_mm_per_hr": parse_float(
                row.get("ksat_mm_per_hr"), field="ksat_mm_per_hr", csv_path=lookup_path, row_number=i
            ),
            "imdmax": parse_float(row.get("imdmax"), field="imdmax", csv_path=lookup_path, row_number=i),
            "notes": (row.get("notes") or "").strip(),
        }
        lookup[key] = rec
        if raw_key in {"-", "DEFAULT", "default"}:
            default_record = rec
    if default_record is None:
        default_record = lookup.get(normalize_key("-")) or lookup.get(normalize_key("default"))
    return lookup, default_record


def require_column(row: dict[str, str], col: str, *, csv_path: Path, row_number: int) -> str:
    if col not in row:
        raise ValueError(f"Missing required column '{col}' in {csv_path}")
    value = (row.get(col) or "").strip()
    if not value:
        raise ValueError(f"Missing value for '{col}' at {csv_path}:{row_number}")
    return value


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Map soil texture/type to SWMM Green-Ampt infiltration parameters (deterministic CSV -> JSON)."
    )
    ap.add_argument("--input", type=Path, required=True, help="Input CSV with subcatchment_id and soil_texture columns.")
    ap.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP, help="Lookup CSV for soil texture mapping.")
    ap.add_argument("--output", type=Path, required=True, help="Output JSON path.")
    ap.add_argument("--subcatchment-column", default="subcatchment_id")
    ap.add_argument("--soil-column", default="soil_texture")
    ap.add_argument("--strict", action="store_true", help="Fail if any soil texture is missing from lookup.")
    args = ap.parse_args()

    input_rows = load_csv_rows(args.input)
    lookup_rows = load_csv_rows(args.lookup)
    lookup_map, default_record = build_lookup(lookup_rows, args.lookup)

    seen_ids: set[str] = set()
    unmatched: set[str] = set()
    records: list[dict[str, Any]] = []
    section_infiltration: list[dict[str, Any]] = []

    for i, row in enumerate(input_rows, start=2):
        subcatchment_id = require_column(row, args.subcatchment_column, csv_path=args.input, row_number=i)
        soil_raw = require_column(row, args.soil_column, csv_path=args.input, row_number=i)
        if subcatchment_id in seen_ids:
            raise ValueError(f"Duplicate subcatchment_id '{subcatchment_id}' in {args.input}")
        seen_ids.add(subcatchment_id)

        key = normalize_key(soil_raw)
        lookup_rec = lookup_map.get(key)
        used_default = False
        if lookup_rec is None:
            if args.strict or default_record is None:
                raise ValueError(
                    f"Unmapped soil texture '{soil_raw}' at {args.input}:{i}. "
                    "Add a lookup row or run without --strict and ensure '-' or DEFAULT exists."
                )
            lookup_rec = default_record
            used_default = True
            unmatched.add(soil_raw)

        infiltration_entry = {
            "id": subcatchment_id,
            "suction_mm": lookup_rec["suction_mm"],
            "ksat_mm_per_hr": lookup_rec["ksat_mm_per_hr"],
            "imdmax": lookup_rec["imdmax"],
        }
        section_infiltration.append(infiltration_entry)
        records.append(
            {
                "subcatchment_id": subcatchment_id,
                "input_soil_texture": soil_raw,
                "lookup_texture": lookup_rec["texture"],
                "used_default": used_default,
                "infiltration": infiltration_entry,
                "notes": lookup_rec["notes"],
            }
        )

    payload = {
        "ok": True,
        "mapping": "soil_to_green_ampt",
        "input_csv": str(args.input),
        "lookup_csv": str(args.lookup),
        "counts": {
            "input_rows": len(input_rows),
            "mapped_rows": len(records),
            "used_default_rows": sum(1 for r in records if r["used_default"]),
        },
        "unmatched_soil_textures": sorted(unmatched),
        "sections": {
            "infiltration": section_infiltration,
        },
        "records": records,
    }
    save_json(args.output, payload)
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(args.output),
                "mapped_rows": payload["counts"]["mapped_rows"],
                "used_default_rows": payload["counts"]["used_default_rows"],
                "unmatched_soil_textures": payload["unmatched_soil_textures"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
