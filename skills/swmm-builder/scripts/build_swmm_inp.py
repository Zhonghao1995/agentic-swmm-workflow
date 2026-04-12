#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def require_float(row: dict[str, str], column: str, *, csv_path: Path, row_number: int) -> float:
    raw = (row.get(column) or "").strip()
    if not raw:
        raise ValueError(f"Missing required value '{column}' at {csv_path}:{row_number}")
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid float for '{column}' at {csv_path}:{row_number}: {raw}") from exc


def read_subcatchments_csv(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f"Subcatchments CSV has no rows: {path}")

    required = ["subcatchment_id", "outlet", "area_ha", "width_m", "slope_pct"]
    for col in required:
        if col not in rows[0]:
            raise ValueError(f"Missing required column '{col}' in {path}")

    out: dict[str, dict[str, Any]] = {}
    for row_num, row in enumerate(rows, start=2):
        subcatchment_id = (row.get("subcatchment_id") or "").strip()
        if not subcatchment_id:
            raise ValueError(f"Blank 'subcatchment_id' at {path}:{row_num}")
        if subcatchment_id in out:
            raise ValueError(f"Duplicate 'subcatchment_id' {subcatchment_id} in {path}")

        outlet = (row.get("outlet") or "").strip()
        if not outlet:
            raise ValueError(f"Blank 'outlet' at {path}:{row_num}")

        rain_gage = (row.get("rain_gage") or "").strip() or None
        curb_raw = (row.get("curb_length_m") or "").strip()
        snow_pack = (row.get("snow_pack") or "").strip()

        curb_length = 0.0
        if curb_raw:
            try:
                curb_length = float(curb_raw)
            except ValueError as exc:
                raise ValueError(f"Invalid curb_length_m at {path}:{row_num}: {curb_raw}") from exc

        rec = {
            "id": subcatchment_id,
            "outlet": outlet,
            "area_ha": require_float(row, "area_ha", csv_path=path, row_number=row_num),
            "width_m": require_float(row, "width_m", csv_path=path, row_number=row_num),
            "slope_pct": require_float(row, "slope_pct", csv_path=path, row_number=row_num),
            "curb_length_m": curb_length,
            "snow_pack": snow_pack,
            "rain_gage": rain_gage,
        }
        out[subcatchment_id] = rec

    return out


def index_by_id(entries: list[dict[str, Any]], *, section: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        raw_id = entry.get("id")
        if raw_id is None:
            raise ValueError(f"Missing 'id' in section '{section}'")
        key = str(raw_id).strip()
        if not key:
            raise ValueError(f"Blank 'id' in section '{section}'")
        if key in out:
            raise ValueError(f"Duplicate id '{key}' in section '{section}'")
        out[key] = entry
    return out


def load_params_sections(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    obj = load_json(path)
    sections = obj.get("sections")
    if not isinstance(sections, dict):
        raise ValueError(f"Missing 'sections' in params JSON: {path}")

    subcatchments = index_by_id(list(sections.get("subcatchments") or []), section="subcatchments")
    subareas = index_by_id(list(sections.get("subareas") or []), section="subareas")
    infiltration = index_by_id(list(sections.get("infiltration") or []), section="infiltration")
    return subcatchments, subareas, infiltration


def parse_timeseries_body(text: str) -> list[str]:
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[TIMESERIES]"):
            continue
        body.append(line.rstrip())
    if not body:
        raise ValueError("Timeseries text is empty")
    return body


def load_climate(
    *,
    rainfall_json_path: Path | None,
    raingage_json_path: Path | None,
    explicit_timeseries_text: Path | None,
    default_gage_id: str,
) -> tuple[dict[str, Any], list[str], Path]:
    rainfall_obj: dict[str, Any] | None = None
    if rainfall_json_path is not None:
        rainfall_obj = load_json(rainfall_json_path)

    timeseries_path: Path | None = None
    if explicit_timeseries_text is not None:
        timeseries_path = explicit_timeseries_text
    elif rainfall_obj is not None:
        out_paths = rainfall_obj.get("outputs") or {}
        candidate = out_paths.get("timeseries_text")
        if candidate:
            timeseries_path = Path(str(candidate))

    if timeseries_path is None:
        raise ValueError("Timeseries source is required. Use --timeseries-text or --rainfall-json with outputs.timeseries_text")

    if not timeseries_path.exists():
        raise ValueError(f"Timeseries text not found: {timeseries_path}")

    series_name = "TS_RAIN"
    if rainfall_obj is not None and rainfall_obj.get("series_name"):
        series_name = str(rainfall_obj.get("series_name"))

    gage: dict[str, Any]
    if raingage_json_path is not None:
        gage_obj = load_json(raingage_json_path)
        gage = dict(gage_obj.get("gage") or {})
        if not gage:
            raise ValueError(f"Missing 'gage' object in {raingage_json_path}")
    else:
        gage = {
            "id": default_gage_id,
            "rain_format": "INTENSITY",
            "interval_min": int(((rainfall_obj or {}).get("range") or {}).get("interval_minutes") or 5),
            "scf": 1.0,
            "source": {
                "kind": "TIMESERIES",
                "series_name": series_name,
            },
        }

    source = gage.get("source") or {}
    if str(source.get("kind") or "").upper() != "TIMESERIES":
        raise ValueError("MVP builder only supports TIMESERIES raingage source")
    source_series = str(source.get("series_name") or "").strip()
    if not source_series:
        raise ValueError("Raingage source.series_name is required")
    if rainfall_obj is not None and str(rainfall_obj.get("series_name") or "") and source_series != str(rainfall_obj.get("series_name")):
        raise ValueError("Raingage series_name does not match rainfall series_name")

    gage["source"] = {"kind": "TIMESERIES", "series_name": source_series}
    gage.setdefault("id", default_gage_id)
    gage.setdefault("rain_format", "INTENSITY")
    gage.setdefault("interval_min", 5)
    gage.setdefault("scf", 1.0)

    timeseries_body = parse_timeseries_body(timeseries_path.read_text(encoding="utf-8"))
    return gage, timeseries_body, timeseries_path


def default_options() -> dict[str, Any]:
    return {
        "FLOW_UNITS": "CMS",
        "INFILTRATION": "GREEN_AMPT",
        "FLOW_ROUTING": "DYNWAVE",
        "LINK_OFFSETS": "DEPTH",
        "MIN_SLOPE": 0,
        "ALLOW_PONDING": "NO",
        "SKIP_STEADY_STATE": "NO",
        "START_DATE": "06/01/2025",
        "START_TIME": "00:00:00",
        "REPORT_START_DATE": "06/01/2025",
        "REPORT_START_TIME": "00:00:00",
        "END_DATE": "06/01/2025",
        "END_TIME": "01:00:00",
        "SWEEP_START": "01/01",
        "SWEEP_END": "12/31",
        "DRY_DAYS": 0,
        "REPORT_STEP": "00:05:00",
        "WET_STEP": "00:01:00",
        "DRY_STEP": "01:00:00",
        "ROUTING_STEP": "00:00:30",
    }


def default_report() -> dict[str, Any]:
    return {
        "INPUT": "NO",
        "CONTROLS": "NO",
        "SUBCATCHMENTS": "ALL",
        "NODES": "ALL",
        "LINKS": "ALL",
    }


def load_builder_config(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    obj = load_json(config_path)
    if not isinstance(obj, dict):
        raise ValueError(f"Config must be a JSON object: {config_path}")
    return obj


def emit_title(config: dict[str, Any]) -> list[str]:
    title = str(config.get("title") or "SWMM model generated by swmm-builder")
    return ["[TITLE]", title]


def emit_options(config: dict[str, Any]) -> list[str]:
    merged = default_options()
    merged.update(config.get("options") or {})
    lines = ["[OPTIONS]", ";;Option             Value"]
    for key, value in merged.items():
        lines.append(f"{key:<20} {format_num(value)}")
    return lines


def emit_report(config: dict[str, Any]) -> list[str]:
    merged = default_report()
    merged.update(config.get("report") or {})
    lines = ["[REPORT]", ";;Option             Value"]
    for key, value in merged.items():
        lines.append(f"{key:<20} {format_num(value)}")
    return lines


def format_interval_hhmm(interval_min: int) -> str:
    if interval_min <= 0:
        raise ValueError("Raingage interval_min must be > 0")
    hh = interval_min // 60
    mm = interval_min % 60
    return f"{hh}:{mm:02d}"


def emit_raingages(gage: dict[str, Any]) -> list[str]:
    line = (
        f"{str(gage['id']):<18} {str(gage['rain_format']):<10} "
        f"{format_interval_hhmm(int(gage['interval_min'])):<10} {format_num(float(gage['scf'])):<8} "
        f"TIMESERIES {str(gage['source']['series_name'])}"
    )
    return [
        "[RAINGAGES]",
        ";;Name             Format     Interval   SCF      Source",
        line,
    ]


def emit_subcatchments(
    subcatchments: dict[str, dict[str, Any]],
    params_subcatchments: dict[str, dict[str, Any]],
    *,
    default_gage_id: str,
) -> list[str]:
    lines = [
        "[SUBCATCHMENTS]",
        ";;Name             Rain Gage          Outlet             Area     %Imperv  Width    %Slope   CurbLen  SnowPack",
    ]
    for subcatchment_id in sorted(subcatchments):
        sc = subcatchments[subcatchment_id]
        p = params_subcatchments[subcatchment_id]
        rain_gage = sc.get("rain_gage") or default_gage_id
        lines.append(
            f"{subcatchment_id:<18} {rain_gage:<18} {sc['outlet']:<18} "
            f"{format_num(sc['area_ha']):<8} {format_num(p['pct_imperv']):<8} {format_num(sc['width_m']):<8} "
            f"{format_num(sc['slope_pct']):<8} {format_num(sc['curb_length_m']):<8} {sc['snow_pack']}"
        )
    return lines


def emit_subareas(subcatchments: dict[str, dict[str, Any]], params_subareas: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "[SUBAREAS]",
        ";;Subcatchment      N-Imperv  N-Perv    S-Imperv S-Perv    PctZero  RouteTo  PctRouted",
    ]
    for subcatchment_id in sorted(subcatchments):
        p = params_subareas[subcatchment_id]
        lines.append(
            f"{subcatchment_id:<18} {format_num(p['n_imperv']):<9} {format_num(p['n_perv']):<9} "
            f"{format_num(p['dstore_imperv_in']):<9} {format_num(p['dstore_perv_in']):<9} "
            f"{format_num(p['zero_imperv_pct']):<8} {str(p['route_to']):<8} {format_num(p['pct_routed'])}"
        )
    return lines


def emit_infiltration(subcatchments: dict[str, dict[str, Any]], params_infiltration: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "[INFILTRATION]",
        ";;Subcatchment      Suction   Ksat      IMDmax",
    ]
    for subcatchment_id in sorted(subcatchments):
        p = params_infiltration[subcatchment_id]
        lines.append(
            f"{subcatchment_id:<18} {format_num(p['suction_mm']):<9} {format_num(p['ksat_mm_per_hr']):<9} {format_num(p['imdmax'])}"
        )
    return lines


def emit_timeseries(timeseries_body: list[str]) -> list[str]:
    return ["[TIMESERIES]", *timeseries_body]


def emit_junctions(network: dict[str, Any]) -> list[str]:
    lines = ["[JUNCTIONS]", ";;Name             Elevation      MaxDepth       InitDepth      SurDepth       Aponded"]
    for j in network.get("junctions", []):
        lines.append(
            f"{j['id']:<18} {format_num(j['invert_elev']):<14} {format_num(j['max_depth']):<14} "
            f"{format_num(j.get('init_depth', 0)):<14} {format_num(j.get('sur_depth', 0)):<14} {format_num(j.get('aponded', 0))}"
        )
    return lines


def emit_outfalls(network: dict[str, Any]) -> list[str]:
    lines = ["[OUTFALLS]", ";;Name             Elevation      Type           Stage Data      Gated          Route To"]
    for o in network.get("outfalls", []):
        lines.append(
            f"{o['id']:<18} {format_num(o['invert_elev']):<14} {str(o['type']):<14} "
            f"{format_num(o.get('stage_data', '')):<15} {format_num(o.get('gated', False)):<14} {format_num(o.get('route_to', ''))}"
        )
    return lines


def emit_conduits(network: dict[str, Any]) -> list[str]:
    lines = [
        "[CONDUITS]",
        ";;Name             From Node         To Node           Length   Roughness InOffset OutOffset InitFlow  MaxFlow",
    ]
    for c in network.get("conduits", []):
        lines.append(
            f"{c['id']:<18} {c['from_node']:<17} {c['to_node']:<17} {format_num(c['length']):<8} "
            f"{format_num(c['roughness']):<9} {format_num(c.get('in_offset', 0)):<8} "
            f"{format_num(c.get('out_offset', 0)):<9} {format_num(c.get('init_flow', 0)):<9} {format_num(c.get('max_flow', ''))}"
        )
    return lines


def emit_xsections(network: dict[str, Any]) -> list[str]:
    lines = ["[XSECTIONS]", ";;Link             Shape          Geom1    Geom2    Geom3    Geom4    Barrels"]
    for c in network.get("conduits", []):
        xs = c["xsection"]
        lines.append(
            f"{c['id']:<18} {xs['shape']:<14} {format_num(xs['geom1']):<8} {format_num(xs.get('geom2', 0)):<8} "
            f"{format_num(xs.get('geom3', 0)):<8} {format_num(xs.get('geom4', 0)):<8} {format_num(xs.get('barrels', 1))}"
        )
    return lines


def emit_coordinates(network: dict[str, Any]) -> list[str]:
    lines = ["[COORDINATES]", ";;Node             X-Coord        Y-Coord"]
    for j in network.get("junctions", []):
        xy = j["coordinates"]
        lines.append(f"{j['id']:<18} {format_num(xy['x']):<14} {format_num(xy['y'])}")
    for o in network.get("outfalls", []):
        xy = o["coordinates"]
        lines.append(f"{o['id']:<18} {format_num(xy['x']):<14} {format_num(xy['y'])}")
    return lines


def emit_vertices(network: dict[str, Any]) -> list[str]:
    lines = ["[VERTICES]", ";;Link             X-Coord        Y-Coord"]
    count = 0
    for c in network.get("conduits", []):
        for v in c.get("vertices", []) or []:
            count += 1
            lines.append(f"{c['id']:<18} {format_num(v['x']):<14} {format_num(v['y'])}")
    return lines if count > 0 else []


def render_inp(
    *,
    config: dict[str, Any],
    gage: dict[str, Any],
    timeseries_body: list[str],
    subcatchments: dict[str, dict[str, Any]],
    params_subcatchments: dict[str, dict[str, Any]],
    params_subareas: dict[str, dict[str, Any]],
    params_infiltration: dict[str, dict[str, Any]],
    network: dict[str, Any],
) -> str:
    blocks: list[list[str]] = [
        emit_title(config),
        emit_options(config),
        emit_raingages(gage),
        emit_subcatchments(subcatchments, params_subcatchments, default_gage_id=str(gage["id"])),
        emit_subareas(subcatchments, params_subareas),
        emit_infiltration(subcatchments, params_infiltration),
        emit_junctions(network),
        emit_outfalls(network),
        emit_conduits(network),
        emit_xsections(network),
        emit_coordinates(network),
        emit_timeseries(timeseries_body),
        emit_report(config),
    ]
    vertices = emit_vertices(network)
    if vertices:
        blocks.insert(11, vertices)

    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def validate_ids(
    subcatchments: dict[str, dict[str, Any]],
    params_subcatchments: dict[str, dict[str, Any]],
    params_subareas: dict[str, dict[str, Any]],
    params_infiltration: dict[str, dict[str, Any]],
    network: dict[str, Any],
) -> dict[str, list[str]]:
    sub_ids = set(subcatchments)
    missing_in_subcatch = sorted(sub_ids - set(params_subcatchments))
    missing_in_subareas = sorted(sub_ids - set(params_subareas))
    missing_in_infiltration = sorted(sub_ids - set(params_infiltration))

    node_ids = {str(j["id"]) for j in network.get("junctions", [])} | {str(o["id"]) for o in network.get("outfalls", [])}
    missing_outlets = sorted([sid for sid, sc in subcatchments.items() if str(sc["outlet"]) not in node_ids])

    return {
        "missing_params_subcatchments": missing_in_subcatch,
        "missing_params_subareas": missing_in_subareas,
        "missing_params_infiltration": missing_in_infiltration,
        "missing_outlet_nodes": missing_outlets,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Assemble runnable SWMM INP from subcatchments CSV + params JSON + network JSON + climate references."
    )
    ap.add_argument("--subcatchments-csv", type=Path, required=True)
    ap.add_argument("--params-json", type=Path, required=True)
    ap.add_argument("--network-json", type=Path, required=True)
    ap.add_argument("--rainfall-json", type=Path, default=None)
    ap.add_argument("--raingage-json", type=Path, default=None)
    ap.add_argument("--timeseries-text", type=Path, default=None)
    ap.add_argument("--config-json", type=Path, default=None)
    ap.add_argument("--default-gage-id", default="RG1")
    ap.add_argument("--out-inp", type=Path, required=True)
    ap.add_argument("--out-manifest", type=Path, required=True)
    args = ap.parse_args()

    subcatchments = read_subcatchments_csv(args.subcatchments_csv)
    params_subcatchments, params_subareas, params_infiltration = load_params_sections(args.params_json)
    network = load_json(args.network_json)
    config = load_builder_config(args.config_json)

    gage, timeseries_body, timeseries_path = load_climate(
        rainfall_json_path=args.rainfall_json,
        raingage_json_path=args.raingage_json,
        explicit_timeseries_text=args.timeseries_text,
        default_gage_id=args.default_gage_id,
    )

    validation = validate_ids(subcatchments, params_subcatchments, params_subareas, params_infiltration, network)
    if any(validation.values()):
        raise ValueError(f"Input consistency checks failed: {json.dumps(validation, ensure_ascii=True)}")

    inp_text = render_inp(
        config=config,
        gage=gage,
        timeseries_body=timeseries_body,
        subcatchments=subcatchments,
        params_subcatchments=params_subcatchments,
        params_subareas=params_subareas,
        params_infiltration=params_infiltration,
        network=network,
    )
    write_text(args.out_inp, inp_text)

    manifest = {
        "ok": True,
        "skill": "swmm-builder",
        "inputs": {
            "subcatchments_csv": {
                "path": str(args.subcatchments_csv),
                "sha256": sha256_file(args.subcatchments_csv),
            },
            "params_json": {
                "path": str(args.params_json),
                "sha256": sha256_file(args.params_json),
            },
            "network_json": {
                "path": str(args.network_json),
                "sha256": sha256_file(args.network_json),
            },
            "rainfall_json": (
                {
                    "path": str(args.rainfall_json),
                    "sha256": sha256_file(args.rainfall_json),
                }
                if args.rainfall_json is not None
                else None
            ),
            "raingage_json": (
                {
                    "path": str(args.raingage_json),
                    "sha256": sha256_file(args.raingage_json),
                }
                if args.raingage_json is not None
                else None
            ),
            "timeseries_text": {
                "path": str(timeseries_path),
                "sha256": sha256_file(timeseries_path),
            },
            "config_json": (
                {
                    "path": str(args.config_json),
                    "sha256": sha256_file(args.config_json),
                }
                if args.config_json is not None
                else None
            ),
        },
        "counts": {
            "subcatchments": len(subcatchments),
            "network_junctions": len(network.get("junctions", [])),
            "network_outfalls": len(network.get("outfalls", [])),
            "network_conduits": len(network.get("conduits", [])),
            "timeseries_rows": len([line for line in timeseries_body if not line.strip().startswith(";;")]),
        },
        "raingage": gage,
        "validation": validation,
        "outputs": {
            "inp": str(args.out_inp),
            "inp_sha256": sha256_file(args.out_inp),
        },
    }
    write_json(args.out_manifest, manifest)

    print(
        json.dumps(
            {
                "ok": True,
                "out_inp": str(args.out_inp),
                "out_manifest": str(args.out_manifest),
                "subcatchments": manifest["counts"]["subcatchments"],
                "network_conduits": manifest["counts"]["network_conduits"],
                "timeseries_rows": manifest["counts"]["timeseries_rows"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
