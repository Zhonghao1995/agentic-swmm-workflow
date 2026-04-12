#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RainRecord:
    timestamp: datetime
    rainfall_mm_per_hr: float


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


def read_records(
    input_csv: Path,
    *,
    timestamp_column: str,
    value_column: str,
    timestamp_format: str,
) -> list[RainRecord]:
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f"CSV has no rows: {input_csv}")

    if timestamp_column not in rows[0]:
        raise ValueError(f"Missing required column '{timestamp_column}' in {input_csv}")
    if value_column not in rows[0]:
        raise ValueError(f"Missing required column '{value_column}' in {input_csv}")

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

        records.append(RainRecord(timestamp=ts, rainfall_mm_per_hr=value))

    return records


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def render_timeseries_lines(series_name: str, records: list[RainRecord]) -> list[str]:
    lines = [
        ";;Name             Date         Time       Value",
    ]
    for rec in records:
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
    ap.add_argument("--input", type=Path, required=True, help="Input rainfall CSV.")
    ap.add_argument("--out-json", type=Path, required=True, help="Output metadata JSON path.")
    ap.add_argument("--out-timeseries", type=Path, required=True, help="Output text path for SWMM [TIMESERIES] body.")
    ap.add_argument("--series-name", default="TS_RAIN")
    ap.add_argument("--timestamp-column", default="timestamp")
    ap.add_argument("--value-column", default="rainfall_mm_per_hr")
    ap.add_argument("--timestamp-format", default="%Y-%m-%d %H:%M")
    args = ap.parse_args()

    records = read_records(
        args.input,
        timestamp_column=args.timestamp_column,
        value_column=args.value_column,
        timestamp_format=args.timestamp_format,
    )

    input_sorted = all(records[i].timestamp < records[i + 1].timestamp for i in range(len(records) - 1))
    records = sorted(records, key=lambda r: r.timestamp)

    timestamps = [rec.timestamp for rec in records]
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("Duplicate timestamps found in rainfall input")

    interval_minutes = estimate_interval_minutes(records)
    timeseries_lines = render_timeseries_lines(args.series_name, records)
    timeseries_text = "\n".join(timeseries_lines) + "\n"

    payload = {
        "ok": True,
        "skill": "swmm-climate",
        "input_csv": str(args.input),
        "input_sha256": sha256_file(args.input),
        "schema": {
            "timestamp_column": args.timestamp_column,
            "value_column": args.value_column,
            "value_units": "mm_per_hr",
        },
        "series_name": args.series_name,
        "counts": {
            "rows": len(records),
            "input_sorted": input_sorted,
        },
        "range": {
            "start": records[0].timestamp.isoformat(timespec="minutes"),
            "end": records[-1].timestamp.isoformat(timespec="minutes"),
            "interval_minutes": interval_minutes,
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
                "series_name": args.series_name,
                "rows": len(records),
                "interval_minutes": interval_minutes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
