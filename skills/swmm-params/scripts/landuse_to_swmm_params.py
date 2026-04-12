#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOOKUP = SKILL_DIR / "references/landuse_class_to_subcatch_params.csv"


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
        raw_key = (row.get("landuse_class") or "").strip()
        if not raw_key:
            raise ValueError(f"Missing 'landuse_class' in lookup at {lookup_path}:{i}")
        key = normalize_key(raw_key)
        if key in lookup:
            raise ValueError(f"Duplicate lookup key '{raw_key}' in {lookup_path}")
        rec = {
            "landuse_class": raw_key,
            "imperv_pct": parse_float(row.get("imperv_pct"), field="imperv_pct", csv_path=lookup_path, row_number=i),
            "n_imperv": parse_float(row.get("n_imperv"), field="n_imperv", csv_path=lookup_path, row_number=i),
            "n_perv": parse_float(row.get("n_perv"), field="n_perv", csv_path=lookup_path, row_number=i),
            "dstore_imperv_in": parse_float(
                row.get("dstore_imperv_in"), field="dstore_imperv_in", csv_path=lookup_path, row_number=i
            ),
            "dstore_perv_in": parse_float(
                row.get("dstore_perv_in"), field="dstore_perv_in", csv_path=lookup_path, row_number=i
            ),
            "zero_imperv_pct": parse_float(
                row.get("zero_imperv_pct"), field="zero_imperv_pct", csv_path=lookup_path, row_number=i
            ),
            "route_to": (row.get("route_to") or "").strip(),
            "pct_routed": parse_float(row.get("pct_routed"), field="pct_routed", csv_path=lookup_path, row_number=i),
            "notes": (row.get("notes") or "").strip(),
        }
        lookup[key] = rec
        if raw_key.upper() == "DEFAULT":
            default_record = rec
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
        description="Map land use class to SWMM runoff/subarea parameters (deterministic CSV -> JSON)."
    )
    ap.add_argument("--input", type=Path, required=True, help="Input CSV with subcatchment_id and landuse_class columns.")
    ap.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP, help="Lookup CSV for land use mapping.")
    ap.add_argument("--output", type=Path, required=True, help="Output JSON path.")
    ap.add_argument("--subcatchment-column", default="subcatchment_id")
    ap.add_argument("--landuse-column", default="landuse_class")
    ap.add_argument("--strict", action="store_true", help="Fail if any land use class is missing from lookup.")
    args = ap.parse_args()

    input_rows = load_csv_rows(args.input)
    lookup_rows = load_csv_rows(args.lookup)
    lookup_map, default_record = build_lookup(lookup_rows, args.lookup)

    seen_ids: set[str] = set()
    unmatched: set[str] = set()
    records: list[dict[str, Any]] = []
    section_subcatchments: list[dict[str, Any]] = []
    section_subareas: list[dict[str, Any]] = []

    for i, row in enumerate(input_rows, start=2):
        subcatchment_id = require_column(row, args.subcatchment_column, csv_path=args.input, row_number=i)
        landuse_raw = require_column(row, args.landuse_column, csv_path=args.input, row_number=i)
        if subcatchment_id in seen_ids:
            raise ValueError(f"Duplicate subcatchment_id '{subcatchment_id}' in {args.input}")
        seen_ids.add(subcatchment_id)

        key = normalize_key(landuse_raw)
        lookup_rec = lookup_map.get(key)
        used_default = False
        if lookup_rec is None:
            if args.strict or default_record is None:
                raise ValueError(
                    f"Unmapped landuse_class '{landuse_raw}' at {args.input}:{i}. "
                    "Add a lookup row or run without --strict and ensure DEFAULT exists."
                )
            lookup_rec = default_record
            used_default = True
            unmatched.add(landuse_raw)

        subcatchment_entry = {
            "id": subcatchment_id,
            "pct_imperv": lookup_rec["imperv_pct"],
        }
        subarea_entry = {
            "id": subcatchment_id,
            "n_imperv": lookup_rec["n_imperv"],
            "n_perv": lookup_rec["n_perv"],
            "dstore_imperv_in": lookup_rec["dstore_imperv_in"],
            "dstore_perv_in": lookup_rec["dstore_perv_in"],
            "zero_imperv_pct": lookup_rec["zero_imperv_pct"],
            "route_to": lookup_rec["route_to"],
            "pct_routed": lookup_rec["pct_routed"],
        }
        section_subcatchments.append(subcatchment_entry)
        section_subareas.append(subarea_entry)
        records.append(
            {
                "subcatchment_id": subcatchment_id,
                "input_landuse_class": landuse_raw,
                "lookup_landuse_class": lookup_rec["landuse_class"],
                "used_default": used_default,
                "subcatchment": subcatchment_entry,
                "subarea": subarea_entry,
                "notes": lookup_rec["notes"],
            }
        )

    payload = {
        "ok": True,
        "mapping": "landuse_to_runoff_subarea",
        "input_csv": str(args.input),
        "lookup_csv": str(args.lookup),
        "counts": {
            "input_rows": len(input_rows),
            "mapped_rows": len(records),
            "used_default_rows": sum(1 for r in records if r["used_default"]),
        },
        "unmatched_landuse_classes": sorted(unmatched),
        "sections": {
            "subcatchments": section_subcatchments,
            "subareas": section_subareas,
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
                "unmatched_landuse_classes": payload["unmatched_landuse_classes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
