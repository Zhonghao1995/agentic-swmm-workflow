#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def interval_hhmm(interval_min: int) -> str:
    if interval_min <= 0:
        raise ValueError("--interval-min must be > 0")
    hours = interval_min // 60
    minutes = interval_min % 60
    return f"{hours}:{minutes:02d}"


def build_raingage_text(
    *,
    gage_id: str,
    rain_format: str,
    interval_min: int,
    scf: float,
    series_name: str,
) -> str:
    hhmm = interval_hhmm(interval_min)
    lines = [
        "[RAINGAGES]",
        ";;Name             Format     Interval   SCF      Source",
        f"{gage_id:<18} {rain_format:<10} {hhmm:<10} {scf:<8g} TIMESERIES {series_name}",
    ]
    return "\n".join(lines) + "\n"


def parse_series_by_station(climate_obj: Any) -> dict[str, str]:
    stations = climate_obj.get("stations")
    if not isinstance(stations, list):
        return {}
    out: dict[str, str] = {}
    for idx, item in enumerate(stations, start=1):
        if not isinstance(item, dict):
            continue
        station_id = str(item.get("station_id") or "").strip()
        series_name = str(item.get("series_name") or "").strip()
        if not station_id or not series_name:
            continue
        if station_id in out:
            raise ValueError(f"Rainfall JSON has duplicate station_id '{station_id}' in stations[{idx}]")
        out[station_id] = series_name
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a deterministic SWMM [RAINGAGES] helper snippet.")
    ap.add_argument("--gage-id", default="RG1")
    ap.add_argument("--series-name", default=None)
    ap.add_argument(
        "--station-id",
        default=None,
        help="Optional station ID when --rainfall-json contains multiple stations.",
    )
    ap.add_argument("--rainfall-json", type=Path, default=None, help="Optional JSON from format_rainfall.py")
    ap.add_argument("--rain-format", default="INTENSITY", choices=["INTENSITY", "VOLUME", "CUMULATIVE"])
    ap.add_argument("--interval-min", type=int, default=5)
    ap.add_argument("--scf", type=float, default=1.0, help="Snow catch deficiency factor")
    ap.add_argument("--out-text", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    args = ap.parse_args()

    series_name = args.series_name
    if args.rainfall_json is not None:
        climate = load_json(args.rainfall_json)
        series_by_station = parse_series_by_station(climate)

        json_series = ""
        if args.station_id is not None:
            requested_station = str(args.station_id).strip()
            if not requested_station:
                raise ValueError("--station-id cannot be blank when provided")
            if not series_by_station:
                raise ValueError(
                    "--station-id was provided, but rainfall JSON does not contain a stations[] mapping"
                )
            if requested_station not in series_by_station:
                known = ", ".join(sorted(series_by_station.keys()))
                raise ValueError(
                    f"Station '{requested_station}' not found in rainfall JSON stations[]; known stations: {known}"
                )
            json_series = series_by_station[requested_station]
        elif len(series_by_station) == 1:
            json_series = next(iter(series_by_station.values()))
        else:
            json_series = str(climate.get("series_name") or "").strip()
            if not json_series and len(series_by_station) > 1:
                known = ", ".join(sorted(series_by_station.keys()))
                raise ValueError(
                    "Rainfall JSON contains multiple stations. Provide --station-id or --series-name. "
                    f"Known stations: {known}"
                )

        if series_name is None:
            series_name = json_series
        elif json_series and json_series != series_name:
            raise ValueError(
                f"--series-name ({series_name}) does not match rainfall JSON series_name ({json_series})"
            )

    if not series_name:
        raise ValueError("A series name is required via --series-name or --rainfall-json")

    snippet = build_raingage_text(
        gage_id=args.gage_id,
        rain_format=args.rain_format,
        interval_min=args.interval_min,
        scf=args.scf,
        series_name=series_name,
    )
    write_text(args.out_text, snippet)

    payload = {
        "ok": True,
        "skill": "swmm-climate",
        "gage": {
            "id": args.gage_id,
            "rain_format": args.rain_format,
            "interval_min": args.interval_min,
            "scf": args.scf,
            "source": {
                "kind": "TIMESERIES",
                "series_name": series_name,
            },
        },
        "source_rainfall_json": str(args.rainfall_json) if args.rainfall_json is not None else None,
        "source_station_id": args.station_id,
        "outputs": {
            "text": str(args.out_text),
        },
    }
    write_json(args.out_json, payload)

    print(
        json.dumps(
            {
                "ok": True,
                "out_text": str(args.out_text),
                "out_json": str(args.out_json),
                "gage_id": args.gage_id,
                "series_name": series_name,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
