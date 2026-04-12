#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
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


def require_non_blank_string(value: Any, *, field: str, context: str) -> str:
    if value is None:
        raise ValueError(f"{context} missing required field '{field}'")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{context} field '{field}' must be a non-blank string")
    return text


def require_number(
    value: Any,
    *,
    field: str,
    context: str,
    min_value: float | None = None,
    max_value: float | None = None,
    min_inclusive: bool = True,
    max_inclusive: bool = True,
) -> float:
    if value is None:
        raise ValueError(f"{context} missing required numeric field '{field}'")

    parsed: float
    if isinstance(value, bool):
        raise ValueError(f"{context} field '{field}' must be numeric, got boolean {value}")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"{context} field '{field}' must be numeric, got blank string")
        try:
            parsed = float(raw)
        except ValueError as exc:
            raise ValueError(f"{context} field '{field}' must be numeric, got: {value}") from exc
    else:
        raise ValueError(f"{context} field '{field}' must be numeric, got type: {type(value).__name__}")

    if min_value is not None:
        if min_inclusive and parsed < min_value:
            raise ValueError(f"{context} field '{field}' must be >= {min_value}, got {parsed}")
        if not min_inclusive and parsed <= min_value:
            raise ValueError(f"{context} field '{field}' must be > {min_value}, got {parsed}")

    if max_value is not None:
        if max_inclusive and parsed > max_value:
            raise ValueError(f"{context} field '{field}' must be <= {max_value}, got {parsed}")
        if not max_inclusive and parsed >= max_value:
            raise ValueError(f"{context} field '{field}' must be < {max_value}, got {parsed}")

    return parsed


def require_int(
    value: Any,
    *,
    field: str,
    context: str,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    parsed_float = require_number(value, field=field, context=context)
    if not parsed_float.is_integer():
        raise ValueError(f"{context} field '{field}' must be an integer, got {parsed_float}")
    parsed = int(parsed_float)
    if min_value is not None and parsed < min_value:
        raise ValueError(f"{context} field '{field}' must be >= {min_value}, got {parsed}")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"{context} field '{field}' must be <= {max_value}, got {parsed}")
    return parsed


def normalize_yes_no(value: Any, *, field: str, context: str) -> str:
    if isinstance(value, bool):
        return "YES" if value else "NO"
    token = require_non_blank_string(value, field=field, context=context).upper()
    if token in {"YES", "TRUE"}:
        return "YES"
    if token in {"NO", "FALSE"}:
        return "NO"
    raise ValueError(f"{context} field '{field}' must be YES/NO or boolean, got {value}")


def parse_clock_time(value: str, *, field: str, context: str, max_hour: int | None = None) -> int:
    token = require_non_blank_string(value, field=field, context=context)
    parts = token.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"{context} field '{field}' must be HH:MM or HH:MM:SS, got {token}")

    if not all(part.isdigit() for part in parts):
        raise ValueError(f"{context} field '{field}' must be HH:MM or HH:MM:SS, got {token}")

    hh = int(parts[0])
    mm = int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    if hh < 0 or mm < 0 or ss < 0:
        raise ValueError(f"{context} field '{field}' must not include negative values, got {token}")
    if mm > 59 or ss > 59:
        raise ValueError(f"{context} field '{field}' has invalid minute/second values, got {token}")
    if max_hour is not None and hh > max_hour:
        raise ValueError(f"{context} field '{field}' hour must be <= {max_hour}, got {token}")
    return hh * 3600 + mm * 60 + ss


def validate_mmddyyyy(value: Any, *, field: str, context: str) -> str:
    token = require_non_blank_string(value, field=field, context=context)
    try:
        datetime.strptime(token, "%m/%d/%Y")
    except ValueError as exc:
        raise ValueError(f"{context} field '{field}' must be mm/dd/yyyy, got {token}") from exc
    return token


def validate_mmdd(value: Any, *, field: str, context: str) -> str:
    token = require_non_blank_string(value, field=field, context=context)
    try:
        datetime.strptime(token, "%m/%d")
    except ValueError as exc:
        raise ValueError(f"{context} field '{field}' must be mm/dd, got {token}") from exc
    return token


def validate_step_time(value: Any, *, field: str, context: str) -> str:
    token = require_non_blank_string(value, field=field, context=context)
    seconds = parse_clock_time(token, field=field, context=context)
    if seconds <= 0:
        raise ValueError(f"{context} field '{field}' must be greater than 00:00:00, got {token}")
    return token


def require_object(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def require_list(value: Any, *, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return value


def read_subcatchments_csv(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f"[SUBCATCHMENTS] CSV has no rows: {path}")

    required = ["subcatchment_id", "outlet", "area_ha", "width_m", "slope_pct"]
    for col in required:
        if col not in rows[0]:
            raise ValueError(f"[SUBCATCHMENTS] missing required column '{col}' in {path}")

    out: dict[str, dict[str, Any]] = {}
    for row_num, row in enumerate(rows, start=2):
        context = f"[SUBCATCHMENTS] CSV row {row_num} ({path})"

        subcatchment_id = require_non_blank_string(row.get("subcatchment_id"), field="subcatchment_id", context=context)
        if subcatchment_id in out:
            raise ValueError(f"[SUBCATCHMENTS] duplicate 'subcatchment_id' {subcatchment_id} in {path}")

        outlet = require_non_blank_string(row.get("outlet"), field="outlet", context=context)

        area_ha = require_number(row.get("area_ha"), field="area_ha", context=context, min_value=0.0, min_inclusive=False)
        width_m = require_number(row.get("width_m"), field="width_m", context=context, min_value=0.0, min_inclusive=False)
        slope_pct = require_number(row.get("slope_pct"), field="slope_pct", context=context, min_value=0.0)

        rain_gage_raw = row.get("rain_gage")
        rain_gage = None
        if rain_gage_raw is not None and str(rain_gage_raw).strip():
            rain_gage = require_non_blank_string(rain_gage_raw, field="rain_gage", context=context)

        curb_length = 0.0
        curb_raw = row.get("curb_length_m")
        if curb_raw is not None and str(curb_raw).strip():
            curb_length = require_number(curb_raw, field="curb_length_m", context=context, min_value=0.0)

        snow_pack_raw = row.get("snow_pack")
        snow_pack = str(snow_pack_raw).strip() if snow_pack_raw is not None else ""

        rec = {
            "id": subcatchment_id,
            "outlet": outlet,
            "area_ha": area_ha,
            "width_m": width_m,
            "slope_pct": slope_pct,
            "curb_length_m": curb_length,
            "snow_pack": snow_pack,
            "rain_gage": rain_gage,
        }
        out[subcatchment_id] = rec

    return out


def index_by_id(entries: list[Any], *, section: str, source_path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"[{section}] entry {idx} in {source_path} must be an object")
        raw_id = entry.get("id")
        if raw_id is None:
            raise ValueError(f"[{section}] entry {idx} in {source_path} is missing required field 'id'")
        key = str(raw_id).strip()
        if not key:
            raise ValueError(f"[{section}] entry {idx} in {source_path} has blank 'id'")
        if key in out:
            raise ValueError(f"[{section}] duplicate id '{key}' in {source_path}")
        out[key] = entry
    return out


def load_params_sections(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    obj = require_object(load_json(path), context=f"Params JSON {path}")
    sections = require_object(obj.get("sections"), context=f"Params JSON {path} field 'sections'")

    subcatchments_entries = require_list(sections.get("subcatchments"), context=f"Params JSON {path} sections.subcatchments")
    subareas_entries = require_list(sections.get("subareas"), context=f"Params JSON {path} sections.subareas")
    infiltration_entries = require_list(sections.get("infiltration"), context=f"Params JSON {path} sections.infiltration")

    subcatchments = index_by_id(subcatchments_entries, section="SUBCATCHMENTS", source_path=path)
    subareas = index_by_id(subareas_entries, section="SUBAREAS", source_path=path)
    infiltration = index_by_id(infiltration_entries, section="INFILTRATION", source_path=path)
    return subcatchments, subareas, infiltration


def validate_and_normalize_params(
    params_subcatchments: dict[str, dict[str, Any]],
    params_subareas: dict[str, dict[str, Any]],
    params_infiltration: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    out_subcatchments: dict[str, dict[str, Any]] = {}
    for subcatchment_id, p in params_subcatchments.items():
        context = f"[SUBCATCHMENTS] params for id '{subcatchment_id}'"
        pct_imperv = require_number(p.get("pct_imperv"), field="pct_imperv", context=context, min_value=0.0, max_value=100.0)
        out_subcatchments[subcatchment_id] = {"id": subcatchment_id, "pct_imperv": pct_imperv}

    out_subareas: dict[str, dict[str, Any]] = {}
    for subcatchment_id, p in params_subareas.items():
        context = f"[SUBAREAS] params for id '{subcatchment_id}'"
        n_imperv = require_number(p.get("n_imperv"), field="n_imperv", context=context, min_value=0.0)
        n_perv = require_number(p.get("n_perv"), field="n_perv", context=context, min_value=0.0)
        dstore_imperv_in = require_number(p.get("dstore_imperv_in"), field="dstore_imperv_in", context=context, min_value=0.0)
        dstore_perv_in = require_number(p.get("dstore_perv_in"), field="dstore_perv_in", context=context, min_value=0.0)
        zero_imperv_pct = require_number(
            p.get("zero_imperv_pct"),
            field="zero_imperv_pct",
            context=context,
            min_value=0.0,
            max_value=100.0,
        )
        route_to = require_non_blank_string(p.get("route_to"), field="route_to", context=context).upper()
        if route_to not in {"OUTLET", "PERVIOUS", "IMPERVIOUS"}:
            raise ValueError(
                f"{context} field 'route_to' must be one of OUTLET/PERVIOUS/IMPERVIOUS, got {p.get('route_to')}"
            )
        pct_routed = require_number(p.get("pct_routed"), field="pct_routed", context=context, min_value=0.0, max_value=100.0)

        out_subareas[subcatchment_id] = {
            "id": subcatchment_id,
            "n_imperv": n_imperv,
            "n_perv": n_perv,
            "dstore_imperv_in": dstore_imperv_in,
            "dstore_perv_in": dstore_perv_in,
            "zero_imperv_pct": zero_imperv_pct,
            "route_to": route_to,
            "pct_routed": pct_routed,
        }

    out_infiltration: dict[str, dict[str, Any]] = {}
    for subcatchment_id, p in params_infiltration.items():
        context = f"[INFILTRATION] params for id '{subcatchment_id}'"
        suction_mm = require_number(p.get("suction_mm"), field="suction_mm", context=context, min_value=0.0, min_inclusive=False)
        ksat_mm_per_hr = require_number(
            p.get("ksat_mm_per_hr"),
            field="ksat_mm_per_hr",
            context=context,
            min_value=0.0,
            min_inclusive=False,
        )
        imdmax = require_number(p.get("imdmax"), field="imdmax", context=context, min_value=0.0, max_value=1.0)

        out_infiltration[subcatchment_id] = {
            "id": subcatchment_id,
            "suction_mm": suction_mm,
            "ksat_mm_per_hr": ksat_mm_per_hr,
            "imdmax": imdmax,
        }

    return out_subcatchments, out_subareas, out_infiltration


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
        raise ValueError("[TIMESERIES] text is empty")
    return body


def parse_timeseries_date(value: str, *, line_number: int) -> datetime:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"[TIMESERIES] line {line_number} has invalid date '{value}' (expected mm/dd/yyyy or yyyy-mm-dd)"
    )


def validate_timeseries_body(timeseries_body: list[str], *, expected_series_name: str) -> dict[str, Any]:
    data_rows = 0
    comment_rows = 0
    previous_ts: datetime | None = None
    datetime_rows = 0

    for idx, raw_line in enumerate(timeseries_body, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(";;"):
            if stripped.startswith(";;"):
                comment_rows += 1
            continue

        parts = stripped.split()
        if len(parts) not in {3, 4}:
            raise ValueError(
                f"[TIMESERIES] line {idx} must have 3 or 4 tokens (name time value | name date time value), got: {stripped}"
            )

        series_name = parts[0]
        if series_name != expected_series_name:
            raise ValueError(
                f"[TIMESERIES] line {idx} series '{series_name}' does not match raingage source series '{expected_series_name}'"
            )

        if len(parts) == 4:
            date_token = parts[1]
            time_token = parts[2]
            value_token = parts[3]
            dt_date = parse_timeseries_date(date_token, line_number=idx)
            seconds = parse_clock_time(time_token, field="time", context=f"[TIMESERIES] line {idx}", max_hour=23)
            current_ts = dt_date.replace(hour=seconds // 3600, minute=(seconds % 3600) // 60, second=seconds % 60)
            datetime_rows += 1
            if previous_ts is not None and current_ts < previous_ts:
                raise ValueError(
                    f"[TIMESERIES] line {idx} datetime {current_ts.isoformat()} is earlier than previous line datetime {previous_ts.isoformat()}"
                )
            previous_ts = current_ts
        else:
            time_token = parts[1]
            value_token = parts[2]
            parse_clock_time(time_token, field="time", context=f"[TIMESERIES] line {idx}", max_hour=23)

        require_number(value_token, field="value", context=f"[TIMESERIES] line {idx}")
        data_rows += 1

    if data_rows == 0:
        raise ValueError("[TIMESERIES] must include at least one data row")

    return {
        "rows_total": len(timeseries_body),
        "rows_data": data_rows,
        "rows_comments": comment_rows,
        "rows_with_datetime": datetime_rows,
    }


def normalize_raingage(
    gage: dict[str, Any],
    *,
    source_label: str,
    rainfall_series_name: str | None,
) -> dict[str, Any]:
    context = f"[RAINGAGES] ({source_label})"
    gage_id = require_non_blank_string(gage.get("id"), field="id", context=context)
    rain_format = require_non_blank_string(gage.get("rain_format"), field="rain_format", context=context).upper()
    if rain_format not in {"INTENSITY", "VOLUME", "CUMULATIVE"}:
        raise ValueError(
            f"{context} field 'rain_format' must be one of INTENSITY/VOLUME/CUMULATIVE, got {gage.get('rain_format')}"
        )

    interval_min = require_int(gage.get("interval_min"), field="interval_min", context=context, min_value=1, max_value=1440)
    scf = require_number(gage.get("scf"), field="scf", context=context, min_value=0.0, min_inclusive=False)

    source = require_object(gage.get("source"), context=f"{context} field 'source'")
    source_kind = require_non_blank_string(source.get("kind"), field="kind", context=f"{context} source").upper()
    if source_kind != "TIMESERIES":
        raise ValueError(f"{context} source.kind must be 'TIMESERIES', got {source.get('kind')}")

    series_name = require_non_blank_string(source.get("series_name"), field="series_name", context=f"{context} source")
    if rainfall_series_name is not None and series_name != rainfall_series_name:
        raise ValueError(
            f"{context} source.series_name '{series_name}' does not match rainfall series_name '{rainfall_series_name}'"
        )

    return {
        "id": gage_id,
        "rain_format": rain_format,
        "interval_min": interval_min,
        "scf": scf,
        "source": {
            "kind": "TIMESERIES",
            "series_name": series_name,
        },
    }


def load_climate(
    *,
    rainfall_json_path: Path | None,
    raingage_json_path: Path | None,
    explicit_timeseries_text: Path | None,
    default_gage_id: str,
) -> tuple[dict[str, Any], list[str], Path, dict[str, Any]]:
    default_gage_id = require_non_blank_string(default_gage_id, field="default_gage_id", context="CLI")

    rainfall_obj: dict[str, Any] | None = None
    rainfall_series_name: str | None = None
    rainfall_interval_min: int | None = None
    if rainfall_json_path is not None:
        rainfall_obj = require_object(load_json(rainfall_json_path), context=f"Rainfall JSON {rainfall_json_path}")
        series_value = rainfall_obj.get("series_name")
        if series_value is not None:
            rainfall_series_name = require_non_blank_string(
                series_value,
                field="series_name",
                context=f"Rainfall JSON {rainfall_json_path}",
            )
        rainfall_range = rainfall_obj.get("range")
        if isinstance(rainfall_range, dict) and rainfall_range.get("interval_minutes") is not None:
            rainfall_interval_min = require_int(
                rainfall_range.get("interval_minutes"),
                field="range.interval_minutes",
                context=f"Rainfall JSON {rainfall_json_path}",
                min_value=1,
            )

    timeseries_path: Path | None = None
    if explicit_timeseries_text is not None:
        timeseries_path = explicit_timeseries_text
    elif rainfall_obj is not None:
        out_paths = rainfall_obj.get("outputs")
        if not isinstance(out_paths, dict):
            raise ValueError(
                f"Rainfall JSON {rainfall_json_path} is missing required object field 'outputs' to resolve timeseries_text"
            )
        candidate = out_paths.get("timeseries_text")
        if candidate:
            timeseries_path = Path(str(candidate))

    if timeseries_path is None:
        raise ValueError("Timeseries source is required. Use --timeseries-text or --rainfall-json with outputs.timeseries_text")

    if not timeseries_path.exists():
        raise ValueError(f"Timeseries text not found: {timeseries_path}")

    default_series_name = rainfall_series_name or "TS_RAIN"
    gage: dict[str, Any]
    if raingage_json_path is not None:
        gage_obj = require_object(load_json(raingage_json_path), context=f"Raingage JSON {raingage_json_path}")
        gage = require_object(gage_obj.get("gage"), context=f"Raingage JSON {raingage_json_path} field 'gage'")
        normalized_gage = normalize_raingage(
            gage,
            source_label=str(raingage_json_path),
            rainfall_series_name=rainfall_series_name,
        )
    else:
        generated_gage = {
            "id": default_gage_id,
            "rain_format": "INTENSITY",
            "interval_min": rainfall_interval_min if rainfall_interval_min is not None else 5,
            "scf": 1.0,
            "source": {
                "kind": "TIMESERIES",
                "series_name": default_series_name,
            },
        }
        normalized_gage = normalize_raingage(
            generated_gage,
            source_label="generated-default",
            rainfall_series_name=rainfall_series_name,
        )

    timeseries_body = parse_timeseries_body(timeseries_path.read_text(encoding="utf-8"))
    timeseries_stats = validate_timeseries_body(timeseries_body, expected_series_name=normalized_gage["source"]["series_name"])
    return normalized_gage, timeseries_body, timeseries_path, timeseries_stats


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

    if "title" in obj and not str(obj.get("title") or "").strip():
        raise ValueError(f"Config field 'title' in {config_path} must be a non-blank string")

    if "options" in obj and not isinstance(obj.get("options"), dict):
        raise ValueError(f"Config field 'options' in {config_path} must be a JSON object")

    if "report" in obj and not isinstance(obj.get("report"), dict):
        raise ValueError(f"Config field 'report' in {config_path} must be a JSON object")

    return obj


def title_from_config(config: dict[str, Any]) -> str:
    return str(config.get("title") or "SWMM model generated by swmm-builder")


def validate_and_merge_options(config: dict[str, Any]) -> dict[str, Any]:
    context = "[OPTIONS]"
    merged = default_options()
    merged.update(config.get("options") or {})

    flow_units = require_non_blank_string(merged.get("FLOW_UNITS"), field="FLOW_UNITS", context=context).upper()
    if flow_units not in {"CFS", "GPM", "MGD", "CMS", "LPS", "MLD"}:
        raise ValueError(f"{context} FLOW_UNITS must be one of CFS/GPM/MGD/CMS/LPS/MLD, got {merged.get('FLOW_UNITS')}")

    infiltration = require_non_blank_string(merged.get("INFILTRATION"), field="INFILTRATION", context=context).upper()
    if infiltration not in {"HORTON", "MODIFIED_HORTON", "GREEN_AMPT", "MODIFIED_GREEN_AMPT", "CURVE_NUMBER"}:
        raise ValueError(
            f"{context} INFILTRATION must be one of HORTON/MODIFIED_HORTON/GREEN_AMPT/MODIFIED_GREEN_AMPT/CURVE_NUMBER, got {merged.get('INFILTRATION')}"
        )

    flow_routing = require_non_blank_string(merged.get("FLOW_ROUTING"), field="FLOW_ROUTING", context=context).upper()
    if flow_routing not in {"STEADY", "KINWAVE", "DYNWAVE"}:
        raise ValueError(f"{context} FLOW_ROUTING must be one of STEADY/KINWAVE/DYNWAVE, got {merged.get('FLOW_ROUTING')}")

    link_offsets = require_non_blank_string(merged.get("LINK_OFFSETS"), field="LINK_OFFSETS", context=context).upper()
    if link_offsets not in {"DEPTH", "ELEVATION"}:
        raise ValueError(f"{context} LINK_OFFSETS must be DEPTH or ELEVATION, got {merged.get('LINK_OFFSETS')}")

    min_slope = require_number(merged.get("MIN_SLOPE"), field="MIN_SLOPE", context=context, min_value=0.0)
    allow_ponding = normalize_yes_no(merged.get("ALLOW_PONDING"), field="ALLOW_PONDING", context=context)
    skip_steady = normalize_yes_no(merged.get("SKIP_STEADY_STATE"), field="SKIP_STEADY_STATE", context=context)

    start_date = validate_mmddyyyy(merged.get("START_DATE"), field="START_DATE", context=context)
    start_time = require_non_blank_string(merged.get("START_TIME"), field="START_TIME", context=context)
    parse_clock_time(start_time, field="START_TIME", context=context, max_hour=23)

    report_start_date = validate_mmddyyyy(merged.get("REPORT_START_DATE"), field="REPORT_START_DATE", context=context)
    report_start_time = require_non_blank_string(merged.get("REPORT_START_TIME"), field="REPORT_START_TIME", context=context)
    parse_clock_time(report_start_time, field="REPORT_START_TIME", context=context, max_hour=23)

    end_date = validate_mmddyyyy(merged.get("END_DATE"), field="END_DATE", context=context)
    end_time = require_non_blank_string(merged.get("END_TIME"), field="END_TIME", context=context)
    parse_clock_time(end_time, field="END_TIME", context=context, max_hour=23)

    sweep_start = validate_mmdd(merged.get("SWEEP_START"), field="SWEEP_START", context=context)
    sweep_end = validate_mmdd(merged.get("SWEEP_END"), field="SWEEP_END", context=context)

    dry_days = require_number(merged.get("DRY_DAYS"), field="DRY_DAYS", context=context, min_value=0.0)

    report_step = validate_step_time(merged.get("REPORT_STEP"), field="REPORT_STEP", context=context)
    wet_step = validate_step_time(merged.get("WET_STEP"), field="WET_STEP", context=context)
    dry_step = validate_step_time(merged.get("DRY_STEP"), field="DRY_STEP", context=context)
    routing_step = validate_step_time(merged.get("ROUTING_STEP"), field="ROUTING_STEP", context=context)

    start_dt = datetime.strptime(f"{start_date} {start_time}", "%m/%d/%Y %H:%M:%S" if len(start_time.split(":")) == 3 else "%m/%d/%Y %H:%M")
    report_start_dt = datetime.strptime(
        f"{report_start_date} {report_start_time}",
        "%m/%d/%Y %H:%M:%S" if len(report_start_time.split(":")) == 3 else "%m/%d/%Y %H:%M",
    )
    end_dt = datetime.strptime(f"{end_date} {end_time}", "%m/%d/%Y %H:%M:%S" if len(end_time.split(":")) == 3 else "%m/%d/%Y %H:%M")

    if end_dt <= start_dt:
        raise ValueError(f"{context} END_DATE/END_TIME must be after START_DATE/START_TIME")
    if report_start_dt < start_dt or report_start_dt > end_dt:
        raise ValueError(f"{context} REPORT_START_DATE/REPORT_START_TIME must be within simulation start/end window")

    validated = dict(merged)
    validated.update(
        {
            "FLOW_UNITS": flow_units,
            "INFILTRATION": infiltration,
            "FLOW_ROUTING": flow_routing,
            "LINK_OFFSETS": link_offsets,
            "MIN_SLOPE": min_slope,
            "ALLOW_PONDING": allow_ponding,
            "SKIP_STEADY_STATE": skip_steady,
            "START_DATE": start_date,
            "START_TIME": start_time,
            "REPORT_START_DATE": report_start_date,
            "REPORT_START_TIME": report_start_time,
            "END_DATE": end_date,
            "END_TIME": end_time,
            "SWEEP_START": sweep_start,
            "SWEEP_END": sweep_end,
            "DRY_DAYS": dry_days,
            "REPORT_STEP": report_step,
            "WET_STEP": wet_step,
            "DRY_STEP": dry_step,
            "ROUTING_STEP": routing_step,
        }
    )
    return validated


def validate_and_merge_report(config: dict[str, Any]) -> dict[str, Any]:
    context = "[REPORT]"
    merged = default_report()
    merged.update(config.get("report") or {})

    validated = dict(merged)
    validated["INPUT"] = normalize_yes_no(merged.get("INPUT"), field="INPUT", context=context)
    validated["CONTROLS"] = normalize_yes_no(merged.get("CONTROLS"), field="CONTROLS", context=context)
    validated["SUBCATCHMENTS"] = require_non_blank_string(merged.get("SUBCATCHMENTS"), field="SUBCATCHMENTS", context=context)
    validated["NODES"] = require_non_blank_string(merged.get("NODES"), field="NODES", context=context)
    validated["LINKS"] = require_non_blank_string(merged.get("LINKS"), field="LINKS", context=context)
    return validated


def emit_title(title: str) -> list[str]:
    return ["[TITLE]", title]


def emit_options(options: dict[str, Any]) -> list[str]:
    lines = ["[OPTIONS]", ";;Option             Value"]
    for key, value in options.items():
        lines.append(f"{key:<20} {format_num(value)}")
    return lines


def emit_report(report: dict[str, Any]) -> list[str]:
    lines = ["[REPORT]", ";;Option             Value"]
    for key, value in report.items():
        lines.append(f"{key:<20} {format_num(value)}")
    return lines


def format_interval_hhmm(interval_min: int) -> str:
    if interval_min <= 0:
        raise ValueError("[RAINGAGES] interval_min must be > 0")
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


def validate_coord(coord: Any, *, context: str) -> dict[str, float]:
    coord_obj = require_object(coord, context=f"{context} coordinates")
    x = require_number(coord_obj.get("x"), field="x", context=f"{context} coordinates")
    y = require_number(coord_obj.get("y"), field="y", context=f"{context} coordinates")
    return {"x": x, "y": y}


def validate_and_normalize_network(network_obj: Any, *, source_path: Path) -> dict[str, Any]:
    network = require_object(network_obj, context=f"Network JSON {source_path}")
    junctions_raw = require_list(network.get("junctions"), context=f"Network JSON {source_path} field 'junctions'")
    outfalls_raw = require_list(network.get("outfalls"), context=f"Network JSON {source_path} field 'outfalls'")
    conduits_raw = require_list(network.get("conduits"), context=f"Network JSON {source_path} field 'conduits'")

    junctions: list[dict[str, Any]] = []
    outfalls: list[dict[str, Any]] = []
    conduits: list[dict[str, Any]] = []

    node_ids: set[str] = set()

    for idx, raw in enumerate(junctions_raw, start=1):
        context = f"[JUNCTIONS] entry {idx} ({source_path})"
        obj = require_object(raw, context=context)
        node_id = require_non_blank_string(obj.get("id"), field="id", context=context)
        if node_id in node_ids:
            raise ValueError(f"{context} duplicate node id '{node_id}' across junctions/outfalls")

        invert_elev = require_number(obj.get("invert_elev"), field="invert_elev", context=context)
        max_depth = require_number(obj.get("max_depth"), field="max_depth", context=context, min_value=0.0)
        init_depth = require_number(obj.get("init_depth", 0.0), field="init_depth", context=context, min_value=0.0)
        sur_depth = require_number(obj.get("sur_depth", 0.0), field="sur_depth", context=context, min_value=0.0)
        aponded = require_number(obj.get("aponded", 0.0), field="aponded", context=context, min_value=0.0)
        coordinates = validate_coord(obj.get("coordinates"), context=context)

        junctions.append(
            {
                "id": node_id,
                "invert_elev": invert_elev,
                "max_depth": max_depth,
                "init_depth": init_depth,
                "sur_depth": sur_depth,
                "aponded": aponded,
                "coordinates": coordinates,
            }
        )
        node_ids.add(node_id)

    for idx, raw in enumerate(outfalls_raw, start=1):
        context = f"[OUTFALLS] entry {idx} ({source_path})"
        obj = require_object(raw, context=context)
        node_id = require_non_blank_string(obj.get("id"), field="id", context=context)
        if node_id in node_ids:
            raise ValueError(f"{context} duplicate node id '{node_id}' across junctions/outfalls")

        invert_elev = require_number(obj.get("invert_elev"), field="invert_elev", context=context)
        outfall_type = require_non_blank_string(obj.get("type"), field="type", context=context).upper()
        if outfall_type not in {"FREE", "NORMAL", "FIXED", "TIDAL", "TIMESERIES"}:
            raise ValueError(f"{context} field 'type' must be FREE/NORMAL/FIXED/TIDAL/TIMESERIES, got {obj.get('type')}")

        stage_data = obj.get("stage_data")
        if outfall_type != "FREE":
            if stage_data is None or str(stage_data).strip() == "":
                raise ValueError(f"{context} requires non-empty 'stage_data' for type {outfall_type}")

        gated_raw = obj.get("gated", False)
        if isinstance(gated_raw, bool):
            gated = gated_raw
        else:
            gated = normalize_yes_no(gated_raw, field="gated", context=context) == "YES"

        route_to = obj.get("route_to")
        route_to_value = ""
        if route_to is not None and str(route_to).strip():
            route_to_value = require_non_blank_string(route_to, field="route_to", context=context)

        coordinates = validate_coord(obj.get("coordinates"), context=context)

        outfalls.append(
            {
                "id": node_id,
                "invert_elev": invert_elev,
                "type": outfall_type,
                "stage_data": stage_data,
                "gated": gated,
                "route_to": route_to_value,
                "coordinates": coordinates,
            }
        )
        node_ids.add(node_id)

    conduit_ids: set[str] = set()
    for idx, raw in enumerate(conduits_raw, start=1):
        context = f"[CONDUITS] entry {idx} ({source_path})"
        obj = require_object(raw, context=context)
        conduit_id = require_non_blank_string(obj.get("id"), field="id", context=context)
        if conduit_id in conduit_ids:
            raise ValueError(f"{context} duplicate conduit id '{conduit_id}'")

        from_node = require_non_blank_string(obj.get("from_node"), field="from_node", context=context)
        to_node = require_non_blank_string(obj.get("to_node"), field="to_node", context=context)
        if from_node == to_node:
            raise ValueError(f"{context} from_node and to_node must differ for conduit '{conduit_id}'")
        if from_node not in node_ids:
            raise ValueError(f"{context} from_node '{from_node}' does not exist in [JUNCTIONS]/[OUTFALLS]")
        if to_node not in node_ids:
            raise ValueError(f"{context} to_node '{to_node}' does not exist in [JUNCTIONS]/[OUTFALLS]")

        length = require_number(obj.get("length"), field="length", context=context, min_value=0.0, min_inclusive=False)
        roughness = require_number(
            obj.get("roughness"),
            field="roughness",
            context=context,
            min_value=0.0,
            min_inclusive=False,
        )
        in_offset = require_number(obj.get("in_offset", 0.0), field="in_offset", context=context)
        out_offset = require_number(obj.get("out_offset", 0.0), field="out_offset", context=context)
        init_flow = require_number(obj.get("init_flow", 0.0), field="init_flow", context=context)

        max_flow_raw = obj.get("max_flow")
        max_flow = None
        if max_flow_raw is not None and str(max_flow_raw).strip() != "":
            max_flow = require_number(
                max_flow_raw,
                field="max_flow",
                context=context,
                min_value=0.0,
                min_inclusive=False,
            )

        xsection_obj = require_object(obj.get("xsection"), context=f"[XSECTIONS] for conduit '{conduit_id}'")
        shape = require_non_blank_string(xsection_obj.get("shape"), field="shape", context=f"[XSECTIONS] conduit '{conduit_id}'").upper()
        geom1 = require_number(
            xsection_obj.get("geom1"),
            field="geom1",
            context=f"[XSECTIONS] conduit '{conduit_id}'",
            min_value=0.0,
            min_inclusive=False,
        )
        geom2 = require_number(xsection_obj.get("geom2", 0.0), field="geom2", context=f"[XSECTIONS] conduit '{conduit_id}'")
        geom3 = require_number(xsection_obj.get("geom3", 0.0), field="geom3", context=f"[XSECTIONS] conduit '{conduit_id}'")
        geom4 = require_number(xsection_obj.get("geom4", 0.0), field="geom4", context=f"[XSECTIONS] conduit '{conduit_id}'")
        barrels = require_int(
            xsection_obj.get("barrels", 1),
            field="barrels",
            context=f"[XSECTIONS] conduit '{conduit_id}'",
            min_value=1,
        )

        vertices_raw = obj.get("vertices")
        vertices: list[dict[str, float]] = []
        if vertices_raw is not None:
            vertices_list = require_list(vertices_raw, context=f"[VERTICES] conduit '{conduit_id}'")
            for v_idx, vertex in enumerate(vertices_list, start=1):
                vertex_obj = require_object(vertex, context=f"[VERTICES] conduit '{conduit_id}' entry {v_idx}")
                x = require_number(vertex_obj.get("x"), field="x", context=f"[VERTICES] conduit '{conduit_id}' entry {v_idx}")
                y = require_number(vertex_obj.get("y"), field="y", context=f"[VERTICES] conduit '{conduit_id}' entry {v_idx}")
                vertices.append({"x": x, "y": y})

        conduits.append(
            {
                "id": conduit_id,
                "from_node": from_node,
                "to_node": to_node,
                "length": length,
                "roughness": roughness,
                "in_offset": in_offset,
                "out_offset": out_offset,
                "init_flow": init_flow,
                "max_flow": max_flow,
                "xsection": {
                    "shape": shape,
                    "geom1": geom1,
                    "geom2": geom2,
                    "geom3": geom3,
                    "geom4": geom4,
                    "barrels": barrels,
                },
                "vertices": vertices,
            }
        )
        conduit_ids.add(conduit_id)

    return {
        "junctions": junctions,
        "outfalls": outfalls,
        "conduits": conduits,
    }


def emit_junctions(network: dict[str, Any]) -> list[str]:
    lines = ["[JUNCTIONS]", ";;Name             Elevation      MaxDepth       InitDepth      SurDepth       Aponded"]
    for j in network.get("junctions", []):
        lines.append(
            f"{j['id']:<18} {format_num(j['invert_elev']):<14} {format_num(j['max_depth']):<14} "
            f"{format_num(j['init_depth']):<14} {format_num(j['sur_depth']):<14} {format_num(j['aponded'])}"
        )
    return lines


def emit_outfalls(network: dict[str, Any]) -> list[str]:
    lines = ["[OUTFALLS]", ";;Name             Elevation      Type           Stage Data      Gated          Route To"]
    for o in network.get("outfalls", []):
        lines.append(
            f"{o['id']:<18} {format_num(o['invert_elev']):<14} {str(o['type']):<14} "
            f"{format_num(o.get('stage_data', '')):<15} {format_num(o['gated']):<14} {format_num(o['route_to'])}"
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
            f"{format_num(c['roughness']):<9} {format_num(c['in_offset']):<8} "
            f"{format_num(c['out_offset']):<9} {format_num(c['init_flow']):<9} {format_num(c['max_flow'])}"
        )
    return lines


def emit_xsections(network: dict[str, Any]) -> list[str]:
    lines = ["[XSECTIONS]", ";;Link             Shape          Geom1    Geom2    Geom3    Geom4    Barrels"]
    for c in network.get("conduits", []):
        xs = c["xsection"]
        lines.append(
            f"{c['id']:<18} {xs['shape']:<14} {format_num(xs['geom1']):<8} {format_num(xs['geom2']):<8} "
            f"{format_num(xs['geom3']):<8} {format_num(xs['geom4']):<8} {format_num(xs['barrels'])}"
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
        for v in c.get("vertices", []):
            count += 1
            lines.append(f"{c['id']:<18} {format_num(v['x']):<14} {format_num(v['y'])}")
    return lines if count > 0 else []


def render_inp(
    *,
    title: str,
    options: dict[str, Any],
    report: dict[str, Any],
    gage: dict[str, Any],
    timeseries_body: list[str],
    subcatchments: dict[str, dict[str, Any]],
    params_subcatchments: dict[str, dict[str, Any]],
    params_subareas: dict[str, dict[str, Any]],
    params_infiltration: dict[str, dict[str, Any]],
    network: dict[str, Any],
) -> str:
    blocks: list[list[str]] = [
        emit_title(title),
        emit_options(options),
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
        emit_report(report),
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
    *,
    gage_id: str,
) -> dict[str, list[str]]:
    sub_ids = set(subcatchments)
    subcatch_ids = set(params_subcatchments)
    subarea_ids = set(params_subareas)
    infiltration_ids = set(params_infiltration)

    missing_in_subcatch = sorted(sub_ids - subcatch_ids)
    missing_in_subareas = sorted(sub_ids - subarea_ids)
    missing_in_infiltration = sorted(sub_ids - infiltration_ids)

    extra_in_subcatch = sorted(subcatch_ids - sub_ids)
    extra_in_subareas = sorted(subarea_ids - sub_ids)
    extra_in_infiltration = sorted(infiltration_ids - sub_ids)

    node_ids = {str(j["id"]) for j in network.get("junctions", [])} | {str(o["id"]) for o in network.get("outfalls", [])}
    missing_outlets = sorted([sid for sid, sc in subcatchments.items() if str(sc["outlet"]) not in node_ids])

    invalid_raingage_refs = sorted(
        [f"{sid}:{sc['rain_gage']}" for sid, sc in subcatchments.items() if sc.get("rain_gage") and str(sc.get("rain_gage")) != gage_id]
    )

    return {
        "missing_params_subcatchments": missing_in_subcatch,
        "missing_params_subareas": missing_in_subareas,
        "missing_params_infiltration": missing_in_infiltration,
        "extra_params_subcatchments": extra_in_subcatch,
        "extra_params_subareas": extra_in_subareas,
        "extra_params_infiltration": extra_in_infiltration,
        "missing_outlet_nodes": missing_outlets,
        "invalid_subcatchment_raingages": invalid_raingage_refs,
    }


def validation_issue_counts(validation: dict[str, list[str]]) -> dict[str, int]:
    return {key: len(value) for key, value in validation.items()}


def build_validation_summary(validation: dict[str, list[str]]) -> str:
    active = [f"{key}={len(value)}" for key, value in validation.items() if value]
    return "; ".join(active)


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
    params_subcatchments, params_subareas, params_infiltration = validate_and_normalize_params(
        params_subcatchments,
        params_subareas,
        params_infiltration,
    )

    network_raw = load_json(args.network_json)
    network = validate_and_normalize_network(network_raw, source_path=args.network_json)

    config = load_builder_config(args.config_json)
    title = title_from_config(config)
    options = validate_and_merge_options(config)
    report = validate_and_merge_report(config)

    gage, timeseries_body, timeseries_path, timeseries_stats = load_climate(
        rainfall_json_path=args.rainfall_json,
        raingage_json_path=args.raingage_json,
        explicit_timeseries_text=args.timeseries_text,
        default_gage_id=args.default_gage_id,
    )

    validation = validate_ids(
        subcatchments,
        params_subcatchments,
        params_subareas,
        params_infiltration,
        network,
        gage_id=str(gage["id"]),
    )
    if any(validation.values()):
        summary = build_validation_summary(validation)
        raise ValueError(
            "Input consistency checks failed: "
            f"{summary}. Details: {json.dumps(validation, ensure_ascii=True)}"
        )

    inp_text = render_inp(
        title=title,
        options=options,
        report=report,
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
            "timeseries_rows": timeseries_stats["rows_data"],
        },
        "raingage": gage,
        "timeseries": {
            "series_name": gage["source"]["series_name"],
            "stats": timeseries_stats,
        },
        "validation": validation,
        "validation_diagnostics": {
            "ok": True,
            "issue_counts": validation_issue_counts(validation),
            "checked_sections": [
                "OPTIONS",
                "RAINGAGES",
                "TIMESERIES",
                "SUBCATCHMENTS",
                "SUBAREAS",
                "INFILTRATION",
                "JUNCTIONS",
                "OUTFALLS",
                "CONDUITS",
                "XSECTIONS",
                "COORDINATES",
                "VERTICES",
            ],
        },
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
