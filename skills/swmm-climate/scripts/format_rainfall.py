#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RainRecord:
    station_id: str
    timestamp: datetime
    rainfall_mm_per_hr: float
    source_file: Path
    source_row: int


ALLOWED_UNITS_CANONICAL = ("mm_per_hr", "in_per_hr")
UNIT_ALIASES = {
    "mm_per_hr": "mm_per_hr",
    "mm/hr": "mm_per_hr",
    "mm/h": "mm_per_hr",
    "mmhr": "mm_per_hr",
    "in_per_hr": "in_per_hr",
    "in/hr": "in_per_hr",
    "in/h": "in_per_hr",
    "inhr": "in_per_hr",
}
UNIT_POLICY_CHOICES = ("strict", "convert_to_mm_per_hr")
TIMESTAMP_POLICY_CHOICES = ("strict", "sort")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: str, explicit_format: str) -> datetime:
    formats = [
        explicit_format,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unsupported timestamp format: '{value}'")


def parse_window_timestamp(value: str | None, *, timestamp_format: str) -> datetime | None:
    if value is None:
        return None
    token = value.strip()
    if not token:
        return None
    return parse_timestamp(token, timestamp_format)


def normalize_units(value: str) -> str:
    token = value.strip().lower().replace(" ", "")
    if token not in UNIT_ALIASES:
        accepted = ", ".join(sorted(ALLOWED_UNITS_CANONICAL))
        raise ValueError(f"Unsupported --value-units '{value}'. Accepted canonical units: {accepted}")
    return UNIT_ALIASES[token]


def convert_to_mm_per_hr(value: float, *, units: str, policy: str) -> float:
    if policy not in UNIT_POLICY_CHOICES:
        raise ValueError(f"Unsupported unit policy: {policy}")
    if units not in ALLOWED_UNITS_CANONICAL:
        raise ValueError(f"Unsupported canonical units: {units}")

    if policy == "strict":
        if units != "mm_per_hr":
            raise ValueError(
                f"Unit policy 'strict' requires mm_per_hr input, got {units}. "
                "Use --unit-policy convert_to_mm_per_hr to convert supported non-SI units."
            )
        return value

    if units == "mm_per_hr":
        return value
    if units == "in_per_hr":
        return value * 25.4
    raise ValueError(f"Cannot convert units: {units}")


def format_location(rec: RainRecord) -> str:
    return f"{rec.source_file}:{rec.source_row}"


def sanitize_series_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    token = token.strip("_")
    return token or "STATION"


def derive_station_id_for_file(
    *,
    input_csv: Path,
    input_count: int,
    user_default_station_id: str | None,
) -> str:
    if input_count == 1:
        if user_default_station_id is not None and not user_default_station_id.strip():
            raise ValueError("--default-station-id cannot be blank")
        return user_default_station_id.strip() if user_default_station_id is not None else "STATION1"

    if user_default_station_id is not None:
        raise ValueError(
            "--default-station-id can only be used with a single --input when --station-column is omitted"
        )
    return input_csv.stem


def resolve_input_paths(*, explicit_inputs: list[Path], input_globs: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for item in explicit_inputs:
        resolved.append(item)

    for pattern in input_globs:
        matches = sorted(glob.glob(pattern))
        if not matches:
            raise ValueError(f"--input-glob pattern matched no files: {pattern}")
        for matched in matches:
            resolved.append(Path(matched))

    if not resolved:
        raise ValueError("At least one input CSV is required via --input (and optionally --input-glob)")

    deduped: list[Path] = []
    seen_real: set[str] = set()
    for path in resolved:
        real = str(path.resolve())
        if real in seen_real:
            continue
        seen_real.add(real)
        if not path.exists():
            raise ValueError(f"Input file not found: {path}")
        if not path.is_file():
            raise ValueError(f"Input path is not a file: {path}")
        deduped.append(path)
    return deduped


def read_records_from_file(
    input_csv: Path,
    *,
    input_count: int,
    timestamp_column: str,
    value_column: str,
    station_column: str | None,
    default_station_id: str | None,
    input_units: str,
    unit_policy: str,
    timestamp_format: str,
) -> list[RainRecord]:
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(f"CSV is missing header row: {input_csv}")
        rows = list(reader)

    if not rows:
        raise ValueError(f"CSV has no rows: {input_csv}")

    if timestamp_column not in fieldnames:
        raise ValueError(f"Missing required column '{timestamp_column}' in {input_csv}")
    if value_column not in fieldnames:
        raise ValueError(f"Missing required column '{value_column}' in {input_csv}")
    if station_column is not None and station_column not in fieldnames:
        raise ValueError(f"Missing required station column '{station_column}' in {input_csv}")

    implicit_station_id = None
    if station_column is None:
        implicit_station_id = derive_station_id_for_file(
            input_csv=input_csv,
            input_count=input_count,
            user_default_station_id=default_station_id,
        )

    records: list[RainRecord] = []
    for idx, row in enumerate(rows, start=2):
        raw_ts = (row.get(timestamp_column) or "").strip()
        raw_value = (row.get(value_column) or "").strip()
        if not raw_ts:
            raise ValueError(f"Blank timestamp at {input_csv}:{idx}")
        if not raw_value:
            raise ValueError(f"Blank rainfall value at {input_csv}:{idx}")

        ts = parse_timestamp(raw_ts, timestamp_format)
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid rainfall value '{raw_value}' at {input_csv}:{idx}") from exc
        if value < 0:
            raise ValueError(f"Rainfall intensity must be >= 0 at {input_csv}:{idx}")

        if station_column is not None:
            station_raw = (row.get(station_column) or "").strip()
            if not station_raw:
                raise ValueError(f"Blank station id in column '{station_column}' at {input_csv}:{idx}")
            station_id = station_raw
        else:
            station_id = str(implicit_station_id)

        converted_value = convert_to_mm_per_hr(value, units=input_units, policy=unit_policy)
        records.append(
            RainRecord(
                station_id=station_id,
                timestamp=ts,
                rainfall_mm_per_hr=converted_value,
                source_file=input_csv,
                source_row=idx,
            )
        )

    return records


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def assign_series_names_by_station(
    *,
    station_ids: list[str],
    base_series_name: str,
    series_name_template: str | None,
) -> dict[str, str]:
    if not station_ids:
        raise ValueError("No stations available for series assignment")

    mapping: dict[str, str] = {}
    if len(station_ids) == 1 and series_name_template is None:
        station = station_ids[0]
        mapping[station] = base_series_name
        return mapping

    for station_id in station_ids:
        station_safe = sanitize_series_token(station_id)
        if series_name_template is None:
            series_name = f"{base_series_name}_{station_safe}"
        else:
            try:
                series_name = series_name_template.format(station=station_id, station_safe=station_safe)
            except KeyError as exc:
                raise ValueError(
                    f"--series-name-template contains unsupported placeholder '{exc.args[0]}'. "
                    "Allowed placeholders: {station}, {station_safe}"
                ) from exc
        series_name = series_name.strip()
        if not series_name:
            raise ValueError(f"Derived blank series name for station '{station_id}'")
        mapping[station_id] = series_name

    reverse: dict[str, str] = {}
    for station_id, series_name in mapping.items():
        if series_name in reverse:
            other_station = reverse[series_name]
            raise ValueError(
                f"Derived duplicate series name '{series_name}' for stations '{other_station}' and '{station_id}'. "
                "Adjust --series-name or --series-name-template."
            )
        reverse[series_name] = station_id
    return mapping


def render_timeseries_lines(
    *,
    station_order: list[str],
    series_by_station: dict[str, str],
    records_by_station: dict[str, list[RainRecord]],
) -> list[str]:
    lines = [
        ";;Name             Date         Time       Value",
    ]
    for station_id in station_order:
        series_name = series_by_station[station_id]
        for rec in records_by_station[station_id]:
            lines.append(
                # SWMM expects calendar dates in mm/dd/yyyy format in [TIMESERIES].
                f"{series_name:<18} {rec.timestamp.strftime('%m/%d/%Y')} {rec.timestamp.strftime('%H:%M')} {format_number(rec.rainfall_mm_per_hr)}"
            )
    return lines


def estimate_interval_minutes(records: list[RainRecord]) -> int | None:
    if len(records) < 2:
        return None
    deltas = [
        int((records[i + 1].timestamp - records[i].timestamp).total_seconds() / 60)
        for i in range(len(records) - 1)
    ]
    if any(d <= 0 for d in deltas):
        raise ValueError("Timestamps must be strictly increasing")
    if len(set(deltas)) == 1:
        return deltas[0]
    return None


def filter_records_by_window(
    records: list[RainRecord],
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[RainRecord]:
    if window_start is None and window_end is None:
        return list(records)

    out: list[RainRecord] = []
    for rec in records:
        if window_start is not None and rec.timestamp < window_start:
            continue
        if window_end is not None and rec.timestamp > window_end:
            continue
        out.append(rec)
    return out


def validate_temporal_consistency(
    records: list[RainRecord],
    *,
    timestamp_policy: str,
) -> dict[str, bool]:
    if timestamp_policy not in TIMESTAMP_POLICY_CHOICES:
        raise ValueError(f"Unsupported --timestamp-policy: {timestamp_policy}")

    previous_by_station: dict[str, RainRecord] = {}
    seen_timestamps_by_station: dict[str, dict[datetime, RainRecord]] = defaultdict(dict)
    input_sorted_by_station: dict[str, bool] = {}

    for rec in records:
        station_id = rec.station_id
        input_sorted_by_station.setdefault(station_id, True)

        seen_for_station = seen_timestamps_by_station[station_id]
        prior_same_ts = seen_for_station.get(rec.timestamp)
        if prior_same_ts is not None:
            raise ValueError(
                f"Duplicate timestamp for station '{station_id}' at {format_location(rec)} and "
                f"{format_location(prior_same_ts)} ({rec.timestamp.isoformat(timespec='minutes')})"
            )
        seen_for_station[rec.timestamp] = rec

        prev = previous_by_station.get(station_id)
        if prev is not None and rec.timestamp <= prev.timestamp:
            input_sorted_by_station[station_id] = False
            if timestamp_policy == "strict":
                raise ValueError(
                    f"Non-monotonic timestamp for station '{station_id}': "
                    f"{format_location(prev)} has {prev.timestamp.isoformat(timespec='minutes')} and "
                    f"{format_location(rec)} has {rec.timestamp.isoformat(timespec='minutes')}. "
                    "Timestamps must be strictly increasing per station."
                )
        previous_by_station[station_id] = rec

    return input_sorted_by_station


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Format rainfall CSV to SWMM-friendly timeseries text + JSON metadata (deterministic)."
    )
    ap.add_argument(
        "--input",
        type=Path,
        action="append",
        default=[],
        help="Input rainfall CSV. Repeat for batch mode.",
    )
    ap.add_argument(
        "--input-glob",
        action="append",
        default=[],
        help="Optional glob pattern for additional input files (e.g. 'data/rain_*.csv'). Repeat as needed.",
    )
    ap.add_argument("--out-json", type=Path, required=True, help="Output metadata JSON path.")
    ap.add_argument("--out-timeseries", type=Path, required=True, help="Output text path for SWMM [TIMESERIES] body.")
    ap.add_argument("--series-name", default="TS_RAIN")
    ap.add_argument(
        "--series-name-template",
        default=None,
        help="Optional template for per-station series names. Placeholders: {station}, {station_safe}.",
    )
    ap.add_argument("--timestamp-column", default="timestamp")
    ap.add_argument("--value-column", default="rainfall_mm_per_hr")
    ap.add_argument(
        "--station-column",
        default=None,
        help="Optional station/gage ID column. If omitted, single-file mode uses one station and multi-file mode derives station ids from file stems.",
    )
    ap.add_argument(
        "--default-station-id",
        default=None,
        help="Optional station id when --station-column is omitted and a single input file is provided.",
    )
    ap.add_argument("--timestamp-format", default="%Y-%m-%d %H:%M")
    ap.add_argument(
        "--window-start",
        default=None,
        help="Optional inclusive event window start timestamp.",
    )
    ap.add_argument(
        "--window-end",
        default=None,
        help="Optional inclusive event window end timestamp.",
    )
    ap.add_argument(
        "--value-units",
        default="mm_per_hr",
        help="Input rainfall units. Accepted canonical aliases: mm_per_hr/mm/hr, in_per_hr/in/hr.",
    )
    ap.add_argument(
        "--unit-policy",
        choices=list(UNIT_POLICY_CHOICES),
        default="strict",
        help="strict: only mm_per_hr accepted. convert_to_mm_per_hr: converts supported units to mm_per_hr.",
    )
    ap.add_argument(
        "--timestamp-policy",
        choices=list(TIMESTAMP_POLICY_CHOICES),
        default="strict",
        help="strict: reject non-monotonic timestamps per station. sort: allow and sort per station.",
    )
    args = ap.parse_args()

    input_paths = resolve_input_paths(explicit_inputs=args.input, input_globs=args.input_glob)
    normalized_input_units = normalize_units(args.value_units)

    window_start = parse_window_timestamp(args.window_start, timestamp_format=args.timestamp_format)
    window_end = parse_window_timestamp(args.window_end, timestamp_format=args.timestamp_format)
    if window_start is not None and window_end is not None and window_start > window_end:
        raise ValueError("--window-start must be <= --window-end")

    all_records: list[RainRecord] = []
    for input_csv in input_paths:
        file_records = read_records_from_file(
            input_csv=input_csv,
            input_count=len(input_paths),
            timestamp_column=args.timestamp_column,
            value_column=args.value_column,
            station_column=args.station_column,
            default_station_id=args.default_station_id,
            input_units=normalized_input_units,
            unit_policy=args.unit_policy,
            timestamp_format=args.timestamp_format,
        )
        all_records.extend(file_records)

    rows_before_window = len(all_records)
    records = filter_records_by_window(
        all_records,
        window_start=window_start,
        window_end=window_end,
    )
    if not records:
        raise ValueError("No records remain after applying optional event window")

    input_sorted_by_station = validate_temporal_consistency(records, timestamp_policy=args.timestamp_policy)

    records_by_station: dict[str, list[RainRecord]] = defaultdict(list)
    for rec in records:
        records_by_station[rec.station_id].append(rec)

    for station_records in records_by_station.values():
        station_records.sort(key=lambda r: r.timestamp)

    station_ids = sorted(records_by_station.keys())
    series_by_station = assign_series_names_by_station(
        station_ids=station_ids,
        base_series_name=args.series_name,
        series_name_template=args.series_name_template,
    )

    station_payloads: list[dict[str, Any]] = []
    all_intervals: list[int | None] = []
    for station_id in station_ids:
        station_records = records_by_station[station_id]
        interval_minutes = estimate_interval_minutes(station_records)
        all_intervals.append(interval_minutes)
        source_files = sorted({str(rec.source_file) for rec in station_records})
        station_payloads.append(
            {
                "station_id": station_id,
                "series_name": series_by_station[station_id],
                "counts": {
                    "rows": len(station_records),
                    "input_sorted": input_sorted_by_station.get(station_id, True),
                },
                "range": {
                    "start": station_records[0].timestamp.isoformat(timespec="minutes"),
                    "end": station_records[-1].timestamp.isoformat(timespec="minutes"),
                    "interval_minutes": interval_minutes,
                },
                "source_files": source_files,
            }
        )

    all_sorted_records = sorted(records, key=lambda r: r.timestamp)
    interval_minutes_global: int | None = None
    if len(station_payloads) == 1:
        interval_minutes_global = all_intervals[0]

    timeseries_lines = render_timeseries_lines(
        station_order=station_ids,
        series_by_station=series_by_station,
        records_by_station=records_by_station,
    )
    timeseries_text = "\n".join(timeseries_lines) + "\n"

    input_sources = [{"path": str(path), "sha256": sha256_file(path)} for path in input_paths]
    series_name_legacy = station_payloads[0]["series_name"] if len(station_payloads) == 1 else None

    payload = {
        "ok": True,
        "skill": "swmm-climate",
        "input_csv": str(input_paths[0]) if len(input_paths) == 1 else None,
        "input_sha256": input_sources[0]["sha256"] if len(input_sources) == 1 else None,
        "inputs": input_sources,
        "schema": {
            "timestamp_column": args.timestamp_column,
            "value_column": args.value_column,
            "station_column": args.station_column,
            "value_units": "mm_per_hr",
            "input_value_units": normalized_input_units,
            "unit_policy": args.unit_policy,
        },
        "window": {
            "start": window_start.isoformat(timespec="minutes") if window_start is not None else None,
            "end": window_end.isoformat(timespec="minutes") if window_end is not None else None,
        },
        "series_name": series_name_legacy,
        "series_names": [station["series_name"] for station in station_payloads],
        "stations": station_payloads,
        "counts": {
            "rows": len(records),
            "rows_before_window": rows_before_window,
            "rows_after_window": len(records),
            "stations": len(station_payloads),
            "input_sorted": all(input_sorted_by_station.get(station_id, True) for station_id in station_ids),
            "input_sorted_by_station": {station_id: input_sorted_by_station.get(station_id, True) for station_id in station_ids},
        },
        "range": {
            "start": all_sorted_records[0].timestamp.isoformat(timespec="minutes"),
            "end": all_sorted_records[-1].timestamp.isoformat(timespec="minutes"),
            "interval_minutes": interval_minutes_global,
        },
        "outputs": {
            "timeseries_text": str(args.out_timeseries),
        },
    }

    write_text(args.out_timeseries, timeseries_text)
    write_json(args.out_json, payload)

    print(
        json.dumps(
            {
                "ok": True,
                "out_json": str(args.out_json),
                "out_timeseries": str(args.out_timeseries),
                "series_name": series_name_legacy,
                "series_names": payload["series_names"],
                "rows": len(records),
                "stations": len(station_payloads),
                "interval_minutes": interval_minutes_global,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
