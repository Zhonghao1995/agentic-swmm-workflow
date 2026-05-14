#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import geopandas as gpd


REPO_ROOT = Path(__file__).resolve().parents[3]
PARAMS_DIR = REPO_ROOT / "skills/swmm-params"
DEFAULT_LANDUSE_LOOKUP = PARAMS_DIR / "references/landuse_class_to_subcatch_params.csv"
DEFAULT_SOIL_LOOKUP = PARAMS_DIR / "references/soil_texture_to_greenampt.csv"
NUMERIC_LANDUSE_FIELDS = [
    "imperv_pct",
    "n_imperv",
    "n_perv",
    "dstore_imperv_in",
    "dstore_perv_in",
    "zero_imperv_pct",
    "pct_routed",
]
NUMERIC_SOIL_FIELDS = ["suction_mm", "ksat_mm_per_hr", "imdmax"]


def normalize_key(value: Any) -> str:
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


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def load_landuse_lookup(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    lookup: dict[str, dict[str, Any]] = {}
    default: dict[str, Any] | None = None
    for i, row in enumerate(load_csv_rows(path), start=2):
        raw = (row.get("landuse_class") or "").strip()
        if not raw:
            raise ValueError(f"Missing 'landuse_class' in lookup at {path}:{i}")
        rec = {
            "landuse_class": raw,
            "route_to": (row.get("route_to") or "").strip(),
            "notes": (row.get("notes") or "").strip(),
        }
        for field in NUMERIC_LANDUSE_FIELDS:
            rec[field] = parse_float(row.get(field), field=field, csv_path=path, row_number=i)
        lookup[normalize_key(raw)] = rec
        if raw.upper() == "DEFAULT":
            default = rec
    return lookup, default


def load_soil_lookup(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    lookup: dict[str, dict[str, Any]] = {}
    default: dict[str, Any] | None = None
    for i, row in enumerate(load_csv_rows(path), start=2):
        raw = (row.get("texture") or "").strip()
        if not raw:
            raise ValueError(f"Missing 'texture' in lookup at {path}:{i}")
        rec = {"texture": raw, "notes": (row.get("notes") or "").strip()}
        for field in NUMERIC_SOIL_FIELDS:
            rec[field] = parse_float(row.get(field), field=field, csv_path=path, row_number=i)
        lookup[normalize_key(raw)] = rec
        if raw in {"-", "DEFAULT", "default"}:
            default = rec
    if default is None:
        default = lookup.get(normalize_key("-")) or lookup.get(normalize_key("default"))
    return lookup, default


def read_vector(path: Path, *, layer_name: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"{layer_name} layer has no features: {path}")
    if gdf.crs is None:
        raise ValueError(f"{layer_name} layer has no CRS: {path}")
    gdf = gdf[gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.buffer(0)
    gdf = gdf[~gdf.geometry.is_empty].copy()
    if gdf.empty:
        raise ValueError(f"{layer_name} layer has no valid polygon geometry after cleanup: {path}")
    return gdf


def ensure_projected(gdf: gpd.GeoDataFrame, *, layer_name: str) -> None:
    if not gdf.crs or not gdf.crs.is_projected:
        raise ValueError(f"{layer_name} CRS must be projected for area weighting, got {gdf.crs}")


def class_area_fractions(
    *,
    subcatchments: gpd.GeoDataFrame,
    thematic: gpd.GeoDataFrame,
    id_field: str,
    class_field: str,
    default_class: str,
    strict: bool,
    label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if class_field not in thematic.columns:
        raise ValueError(f"{label} field '{class_field}' not found. Available fields: {list(thematic.columns)}")

    sub = subcatchments[[id_field, "geometry"]].copy()
    sub["sub_area_m2"] = sub.geometry.area
    thematic = thematic[[class_field, "geometry"]].copy()
    if thematic.crs != sub.crs:
        thematic = thematic.to_crs(sub.crs)

    inter = gpd.overlay(sub, thematic, how="intersection", keep_geom_type=False)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, float]] = {}
    sub_areas = {str(row[id_field]): float(row["sub_area_m2"]) for _, row in sub.iterrows()}

    if not inter.empty:
        inter["intersect_area_m2"] = inter.geometry.area
        for _, row in inter.iterrows():
            sid = str(row[id_field])
            raw_class = str(row.get(class_field) or default_class).strip() or default_class
            area = float(row["intersect_area_m2"])
            if area <= 0:
                continue
            grouped.setdefault(sid, {})
            grouped[sid][raw_class] = grouped[sid].get(raw_class, 0.0) + area

    for sid, sub_area in sub_areas.items():
        if sub_area <= 0:
            raise ValueError(f"Subcatchment '{sid}' has non-positive area")
        class_areas = grouped.get(sid, {})
        covered = sum(class_areas.values())
        if covered <= 0:
            if strict:
                raise ValueError(f"Subcatchment '{sid}' has no {label} overlay coverage")
            class_areas = {default_class: sub_area}
            covered = sub_area
            issues.append({"severity": "warning", "id": sid, "message": f"No {label} coverage; used {default_class}."})
        elif covered < sub_area * 0.999:
            remainder = sub_area - covered
            class_areas[default_class] = class_areas.get(default_class, 0.0) + remainder
            issues.append(
                {
                    "severity": "warning",
                    "id": sid,
                    "message": f"{label} coverage below subcatchment area; assigned uncovered area to {default_class}.",
                    "coverage_fraction": covered / sub_area,
                }
            )
        elif covered > sub_area * 1.001:
            issues.append(
                {
                    "severity": "warning",
                    "id": sid,
                    "message": f"{label} overlay areas exceed subcatchment area; normalized fractions.",
                    "coverage_fraction": covered / sub_area,
                }
            )

        denominator = sum(class_areas.values())
        for class_name, area in sorted(class_areas.items()):
            fraction = area / denominator if denominator > 0 else 0.0
            if fraction <= 0:
                continue
            rows.append(
                {
                    "subcatchment_id": sid,
                    "class": class_name,
                    "area_m2": area,
                    "fraction": fraction,
                }
            )

    return rows, issues


def weighted_landuse(
    rows: list[dict[str, Any]],
    lookup: dict[str, dict[str, Any]],
    default: dict[str, Any] | None,
    *,
    strict: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(row["subcatchment_id"], []).append(row)

    records: list[dict[str, Any]] = []
    subcatchment_section: list[dict[str, Any]] = []
    subarea_section: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    unmatched: set[str] = set()

    for sid in sorted(by_id):
        weighted = {field: 0.0 for field in NUMERIC_LANDUSE_FIELDS}
        route_votes: dict[str, float] = {}
        components = []
        for row in by_id[sid]:
            class_name = row["class"]
            fraction = float(row["fraction"])
            rec = lookup.get(normalize_key(class_name))
            used_default = False
            if rec is None:
                if strict or default is None:
                    raise ValueError(f"Unmapped landuse class '{class_name}' for subcatchment '{sid}'")
                rec = default
                used_default = True
                unmatched.add(class_name)
            for field in NUMERIC_LANDUSE_FIELDS:
                weighted[field] += rec[field] * fraction
            route_to = rec["route_to"] or "OUTLET"
            route_votes[route_to] = route_votes.get(route_to, 0.0) + fraction
            components.append(
                {
                    "class": class_name,
                    "lookup_class": rec["landuse_class"],
                    "fraction": fraction,
                    "area_m2": row["area_m2"],
                    "used_default": used_default,
                }
            )
            audit.append(
                {
                    "subcatchment_id": sid,
                    "landuse_class": class_name,
                    "lookup_landuse_class": rec["landuse_class"],
                    "area_m2": round(float(row["area_m2"]), 6),
                    "fraction": round(fraction, 8),
                    "used_default": used_default,
                }
            )
        route_to = max(route_votes.items(), key=lambda item: item[1])[0]
        subcatchment_entry = {"id": sid, "pct_imperv": round(weighted["imperv_pct"], 6)}
        subarea_entry = {
            "id": sid,
            "n_imperv": round(weighted["n_imperv"], 6),
            "n_perv": round(weighted["n_perv"], 6),
            "dstore_imperv_in": round(weighted["dstore_imperv_in"], 6),
            "dstore_perv_in": round(weighted["dstore_perv_in"], 6),
            "zero_imperv_pct": round(weighted["zero_imperv_pct"], 6),
            "route_to": route_to,
            "pct_routed": round(weighted["pct_routed"], 6),
        }
        subcatchment_section.append(subcatchment_entry)
        subarea_section.append(subarea_entry)
        records.append(
            {
                "subcatchment_id": sid,
                "method": "area_weighted_landuse_parameters",
                "components": components,
                "subcatchment": subcatchment_entry,
                "subarea": subarea_entry,
            }
        )

    return (
        {
            "ok": True,
            "mapping": "area_weighted_landuse_to_runoff_subarea",
            "sections": {"subcatchments": subcatchment_section, "subareas": subarea_section},
            "records": records,
            "unmatched_landuse_classes": sorted(unmatched),
        },
        audit,
        unmatched,
    )


def weighted_soil(
    rows: list[dict[str, Any]],
    lookup: dict[str, dict[str, Any]],
    default: dict[str, Any] | None,
    *,
    strict: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(row["subcatchment_id"], []).append(row)

    records: list[dict[str, Any]] = []
    infiltration_section: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    unmatched: set[str] = set()

    for sid in sorted(by_id):
        weighted = {field: 0.0 for field in NUMERIC_SOIL_FIELDS}
        components = []
        for row in by_id[sid]:
            texture = row["class"]
            fraction = float(row["fraction"])
            rec = lookup.get(normalize_key(texture))
            used_default = False
            if rec is None:
                if strict or default is None:
                    raise ValueError(f"Unmapped soil texture '{texture}' for subcatchment '{sid}'")
                rec = default
                used_default = True
                unmatched.add(texture)
            for field in NUMERIC_SOIL_FIELDS:
                weighted[field] += rec[field] * fraction
            components.append(
                {
                    "texture": texture,
                    "lookup_texture": rec["texture"],
                    "fraction": fraction,
                    "area_m2": row["area_m2"],
                    "used_default": used_default,
                }
            )
            audit.append(
                {
                    "subcatchment_id": sid,
                    "soil_texture": texture,
                    "lookup_texture": rec["texture"],
                    "area_m2": round(float(row["area_m2"]), 6),
                    "fraction": round(fraction, 8),
                    "used_default": used_default,
                }
            )
        entry = {
            "id": sid,
            "suction_mm": round(weighted["suction_mm"], 6),
            "ksat_mm_per_hr": round(weighted["ksat_mm_per_hr"], 6),
            "imdmax": round(weighted["imdmax"], 6),
        }
        infiltration_section.append(entry)
        records.append(
            {
                "subcatchment_id": sid,
                "method": "area_weighted_soil_green_ampt_parameters",
                "components": components,
                "infiltration": entry,
            }
        )

    return (
        {
            "ok": True,
            "mapping": "area_weighted_soil_to_green_ampt",
            "sections": {"infiltration": infiltration_section},
            "records": records,
            "unmatched_soil_textures": sorted(unmatched),
        },
        audit,
        unmatched,
    )


def merge_params(landuse: dict[str, Any], soil: dict[str, Any]) -> dict[str, Any]:
    subcatchments = {row["id"]: row for row in landuse["sections"]["subcatchments"]}
    subareas = {row["id"]: row for row in landuse["sections"]["subareas"]}
    infiltration = {row["id"]: row for row in soil["sections"]["infiltration"]}
    all_ids = sorted(set(subcatchments) | set(subareas) | set(infiltration))
    incomplete = []
    by_subcatchment = []
    for sid in all_ids:
        rec: dict[str, Any] = {"id": sid}
        missing = []
        for key, source, section in [
            ("subcatchment", subcatchments, "subcatchments"),
            ("subarea", subareas, "subareas"),
            ("infiltration", infiltration, "infiltration"),
        ]:
            if sid in source:
                rec[key] = source[sid]
            else:
                missing.append(section)
        if missing:
            rec["missing_sections"] = missing
            incomplete.append({"id": sid, "missing_sections": missing})
        by_subcatchment.append(rec)

    return {
        "ok": True,
        "mapping": "merged_area_weighted_swmm_params",
        "counts": {
            "subcatchment_count": len(all_ids),
            "subcatchments_with_subcatchment_section": len(subcatchments),
            "subcatchments_with_subarea_section": len(subareas),
            "subcatchments_with_infiltration_section": len(infiltration),
            "incomplete_subcatchment_count": len(incomplete),
        },
        "incomplete_ids": incomplete,
        "sections": {
            "subcatchments": [subcatchments[sid] for sid in sorted(subcatchments)],
            "subareas": [subareas[sid] for sid in sorted(subareas)],
            "infiltration": [infiltration[sid] for sid in sorted(infiltration)],
        },
        "by_subcatchment": by_subcatchment,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build area-weighted SWMM params from subcatchment/landuse/soil polygon overlays.")
    ap.add_argument("--subcatchments", type=Path, required=True)
    ap.add_argument("--landuse", type=Path, required=True)
    ap.add_argument("--soil", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--id-field", default="basin_id")
    ap.add_argument("--landuse-field", default="CLASS")
    ap.add_argument("--soil-field", default="TEXTURE")
    ap.add_argument("--landuse-lookup", type=Path, default=DEFAULT_LANDUSE_LOOKUP)
    ap.add_argument("--soil-lookup", type=Path, default=DEFAULT_SOIL_LOOKUP)
    ap.add_argument("--strict", action="store_true", help="Fail on missing overlay coverage or missing lookup classes.")
    args = ap.parse_args()

    sub = read_vector(args.subcatchments, layer_name="subcatchments")
    landuse = read_vector(args.landuse, layer_name="landuse")
    soil = read_vector(args.soil, layer_name="soil")
    if args.id_field == "__feature_id__":
        sub = sub.reset_index(drop=True)
        sub[args.id_field] = [f"S{i + 1}" for i in range(len(sub))]
    elif args.id_field not in sub.columns:
        raise ValueError(f"Subcatchment id field '{args.id_field}' not found. Available fields: {list(sub.columns)}")
    ensure_projected(sub, layer_name="subcatchments")
    sub[args.id_field] = sub[args.id_field].astype(str)
    duplicate_ids = sorted(sub.loc[sub[args.id_field].duplicated(), args.id_field].unique())
    if duplicate_ids:
        sample = ", ".join(duplicate_ids[:10])
        raise ValueError(
            f"Subcatchment id field '{args.id_field}' is not unique; duplicate values include: {sample}. "
            "Use a unique id field or pass --id-field __feature_id__ to generate one id per feature."
        )

    land_lookup, land_default = load_landuse_lookup(args.landuse_lookup)
    soil_lookup, soil_default = load_soil_lookup(args.soil_lookup)
    if land_default is None and not args.strict:
        raise ValueError(f"Landuse lookup has no DEFAULT row: {args.landuse_lookup}")
    if soil_default is None and not args.strict:
        raise ValueError(f"Soil lookup has no '-' or DEFAULT row: {args.soil_lookup}")

    land_rows, land_issues = class_area_fractions(
        subcatchments=sub,
        thematic=landuse,
        id_field=args.id_field,
        class_field=args.landuse_field,
        default_class="DEFAULT",
        strict=args.strict,
        label="landuse",
    )
    soil_rows, soil_issues = class_area_fractions(
        subcatchments=sub,
        thematic=soil,
        id_field=args.id_field,
        class_field=args.soil_field,
        default_class="-",
        strict=args.strict,
        label="soil",
    )

    land_payload, land_audit, unmatched_land = weighted_landuse(land_rows, land_lookup, land_default, strict=args.strict)
    soil_payload, soil_audit, unmatched_soil = weighted_soil(soil_rows, soil_lookup, soil_default, strict=args.strict)
    merged = merge_params(land_payload, soil_payload)
    merged["sources"] = {
        "subcatchments": str(args.subcatchments),
        "landuse": str(args.landuse),
        "soil": str(args.soil),
        "landuse_lookup": str(args.landuse_lookup),
        "soil_lookup": str(args.soil_lookup),
    }
    merged["area_weighting"] = {
        "method": "polygon_intersection_area_fraction",
        "subcatchment_id_field": args.id_field,
        "landuse_field": args.landuse_field,
        "soil_field": args.soil_field,
        "missing_landuse_area_policy": "DEFAULT",
        "missing_soil_area_policy": "-",
        "soil_ksat_policy": "linear_area_weighted_first_draft",
    }
    merged["issues"] = land_issues + soil_issues
    merged["unmatched_landuse_classes"] = sorted(unmatched_land)
    merged["unmatched_soil_textures"] = sorted(unmatched_soil)

    # Build structured warnings: per-unmatched-class, sum of area routed
    # through the DEFAULT row. Lets framework_mcp_manifest promote these
    # automatically into missing_or_fallback_inputs.
    def _summarise_default_use(audit_rows, source_field, fallback_class):
        per_class: dict[str, float] = {}
        for row in audit_rows:
            if not row.get("used_default"):
                continue
            raw_class = row.get(source_field) or "<missing>"
            per_class[raw_class] = per_class.get(raw_class, 0.0) + float(row.get("area_m2") or 0.0)
        return [
            {
                "code": f"{source_field}_unmatched",
                "value": cls,
                "fallback_class": fallback_class,
                "fallback_area_m2": area,
            }
            for cls, area in sorted(per_class.items())
        ]

    warnings: list[dict[str, Any]] = []
    warnings.extend(_summarise_default_use(land_audit, "landuse_class", "DEFAULT"))
    warnings.extend(_summarise_default_use(soil_audit, "soil_texture", "DEFAULT"))
    merged["warnings"] = warnings

    out_dir = args.out_dir
    write_json(out_dir / "landuse_weighted_params.json", land_payload)
    write_json(out_dir / "soil_weighted_params.json", soil_payload)
    write_json(out_dir / "weighted_params.json", merged)
    write_csv(
        out_dir / "landuse_area_weights.csv",
        land_audit,
        ["subcatchment_id", "landuse_class", "lookup_landuse_class", "area_m2", "fraction", "used_default"],
    )
    write_csv(
        out_dir / "soil_area_weights.csv",
        soil_audit,
        ["subcatchment_id", "soil_texture", "lookup_texture", "area_m2", "fraction", "used_default"],
    )

    print(
        json.dumps(
            {
                "ok": True,
                "out_dir": str(out_dir),
                "weighted_params_json": str(out_dir / "weighted_params.json"),
                "landuse_area_weights_csv": str(out_dir / "landuse_area_weights.csv"),
                "soil_area_weights_csv": str(out_dir / "soil_area_weights.csv"),
                "subcatchment_count": merged["counts"]["subcatchment_count"],
                "issue_count": len(merged["issues"]),
                "unmatched_landuse_classes": merged["unmatched_landuse_classes"],
                "unmatched_soil_textures": merged["unmatched_soil_textures"],
                "warnings": warnings,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
