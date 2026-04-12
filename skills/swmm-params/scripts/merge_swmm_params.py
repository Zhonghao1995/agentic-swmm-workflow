#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def index_by_id(entries: list[dict[str, Any]], *, section: str) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for entry in entries:
        raw_id = entry.get("id")
        if raw_id is None:
            raise ValueError(f"Missing 'id' in section '{section}'")
        subcatchment_id = str(raw_id).strip()
        if not subcatchment_id:
            raise ValueError(f"Blank 'id' in section '{section}'")
        if subcatchment_id in idx:
            raise ValueError(f"Duplicate id '{subcatchment_id}' in section '{section}'")
        idx[subcatchment_id] = entry
    return idx


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Merge land use and soil mapping outputs into one explicit JSON payload for future swmm-builder."
    )
    ap.add_argument("--landuse-json", type=Path, default=None, help="Output JSON from landuse_to_swmm_params.py")
    ap.add_argument("--soil-json", type=Path, default=None, help="Output JSON from soil_to_greenampt.py")
    ap.add_argument("--output", type=Path, required=True, help="Output merged JSON path.")
    ap.add_argument("--strict", action="store_true", help="Fail if any subcatchment is missing one or more sections.")
    args = ap.parse_args()

    if args.landuse_json is None and args.soil_json is None:
        raise ValueError("At least one source is required: --landuse-json and/or --soil-json")

    landuse_subcatchments: dict[str, dict[str, Any]] = {}
    landuse_subareas: dict[str, dict[str, Any]] = {}
    soil_infiltration: dict[str, dict[str, Any]] = {}

    if args.landuse_json is not None:
        landuse_obj = load_json(args.landuse_json)
        sections = landuse_obj.get("sections", {})
        landuse_subcatchments = index_by_id(sections.get("subcatchments", []), section="subcatchments")
        landuse_subareas = index_by_id(sections.get("subareas", []), section="subareas")

    if args.soil_json is not None:
        soil_obj = load_json(args.soil_json)
        sections = soil_obj.get("sections", {})
        soil_infiltration = index_by_id(sections.get("infiltration", []), section="infiltration")

    all_ids = sorted(set(landuse_subcatchments) | set(landuse_subareas) | set(soil_infiltration))

    by_subcatchment: list[dict[str, Any]] = []
    incomplete_ids: list[dict[str, Any]] = []
    for subcatchment_id in all_ids:
        rec: dict[str, Any] = {"id": subcatchment_id}
        missing: list[str] = []

        subcatchment = landuse_subcatchments.get(subcatchment_id)
        if subcatchment is not None:
            rec["subcatchment"] = subcatchment
        else:
            missing.append("subcatchments")

        subarea = landuse_subareas.get(subcatchment_id)
        if subarea is not None:
            rec["subarea"] = subarea
        else:
            missing.append("subareas")

        infiltration = soil_infiltration.get(subcatchment_id)
        if infiltration is not None:
            rec["infiltration"] = infiltration
        else:
            missing.append("infiltration")

        if missing:
            rec["missing_sections"] = missing
            incomplete_ids.append({"id": subcatchment_id, "missing_sections": missing})

        by_subcatchment.append(rec)

    if args.strict and incomplete_ids:
        raise ValueError(
            f"Found incomplete subcatchment mappings under --strict: {json.dumps(incomplete_ids, ensure_ascii=True)}"
        )

    payload = {
        "ok": True,
        "mapping": "merged_swmm_params",
        "sources": {
            "landuse_json": str(args.landuse_json) if args.landuse_json is not None else None,
            "soil_json": str(args.soil_json) if args.soil_json is not None else None,
        },
        "counts": {
            "subcatchment_count": len(all_ids),
            "subcatchments_with_subcatchment_section": len(landuse_subcatchments),
            "subcatchments_with_subarea_section": len(landuse_subareas),
            "subcatchments_with_infiltration_section": len(soil_infiltration),
            "incomplete_subcatchment_count": len(incomplete_ids),
        },
        "incomplete_ids": incomplete_ids,
        "sections": {
            "subcatchments": [landuse_subcatchments[sid] for sid in sorted(landuse_subcatchments)],
            "subareas": [landuse_subareas[sid] for sid in sorted(landuse_subareas)],
            "infiltration": [soil_infiltration[sid] for sid in sorted(soil_infiltration)],
        },
        "by_subcatchment": by_subcatchment,
    }

    save_json(args.output, payload)
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(args.output),
                "subcatchment_count": payload["counts"]["subcatchment_count"],
                "incomplete_subcatchment_count": payload["counts"]["incomplete_subcatchment_count"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
