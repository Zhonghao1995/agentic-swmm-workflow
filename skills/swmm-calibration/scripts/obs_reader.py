#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pandas as pd

TIME_CANDIDATES = ["timestamp", "time", "datetime", "date_time", "date"]
FLOW_CANDIDATES = ["flow", "discharge", "q", "value"]


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _non_comment_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(";;"):
            continue
        lines.append(raw)
    return lines


def detect_delimiter(lines: list[str]) -> str:
    sample = "\n".join(lines[:5])
    if "," in sample:
        return ","
    if "\t" in sample:
        return "\t"
    return r"\s+"


def _looks_headerless_datetime_flow(lines: list[str]) -> bool:
    if not lines:
        return False
    first = lines[0].strip().split()
    if len(first) != 3:
        return False
    dt = pd.to_datetime(f"{first[0]} {first[1]}", errors="coerce")
    flow = pd.to_numeric(first[2], errors="coerce")
    return pd.notna(dt) and pd.notna(flow)


def _read_headerless_datetime_flow(lines: list[str], time_format: str | None = None) -> pd.DataFrame:
    rows = []
    for raw in lines:
        parts = raw.strip().split()
        if len(parts) < 3:
            continue
        dt = pd.to_datetime(f"{parts[0]} {parts[1]}", format=time_format, errors="coerce")
        flow = pd.to_numeric(parts[2], errors="coerce")
        if pd.notna(dt) and pd.notna(flow):
            rows.append((dt, float(flow)))
    if not rows:
        raise ValueError("No valid datetime/flow rows found in headerless observed file")
    out = pd.DataFrame(rows, columns=["timestamp", "flow"])
    return out.sort_values("timestamp").reset_index(drop=True)


def read_series(path: str | Path, timestamp_col: str | None = None, flow_col: str | None = None, time_format: str | None = None) -> pd.DataFrame:
    p = Path(path)
    lines = _non_comment_lines(p)
    if not lines:
        raise ValueError(f"No data rows found in {p}")

    if _looks_headerless_datetime_flow(lines):
        return _read_headerless_datetime_flow(lines, time_format=time_format)

    delim = detect_delimiter(lines)
    df = pd.read_csv(io.StringIO("\n".join(lines)), sep=delim, engine="python")
    if df.empty:
        raise ValueError(f"No data rows found in {p}")

    cols = {c: _normalize(c) for c in df.columns}
    rev = {v: k for k, v in cols.items()}

    if timestamp_col is None:
        timestamp_col = next((rev[c] for c in cols.values() if c in TIME_CANDIDATES), None)
    if flow_col is None:
        flow_col = next((rev[c] for c in cols.values() if c in FLOW_CANDIDATES), None)

    if timestamp_col is None or flow_col is None:
        raise ValueError(f"Could not infer timestamp/flow columns from {list(df.columns)}")

    out = df[[timestamp_col, flow_col]].copy()
    out.columns = ["timestamp", "flow"]
    out["timestamp"] = pd.to_datetime(out["timestamp"], format=time_format, errors="coerce")
    out["flow"] = pd.to_numeric(out["flow"], errors="coerce")
    out = out.dropna(subset=["timestamp", "flow"]).sort_values("timestamp")
    return out.reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--timestamp-col", default=None)
    ap.add_argument("--flow-col", default=None)
    ap.add_argument("--time-format", default=None)
    args = ap.parse_args()
    df = read_series(args.path, timestamp_col=args.timestamp_col, flow_col=args.flow_col, time_format=args.time_format)
    print(json.dumps({
        "rows": int(len(df)),
        "start": df.iloc[0]["timestamp"].isoformat() if len(df) else None,
        "end": df.iloc[-1]["timestamp"].isoformat() if len(df) else None,
        "columns": ["timestamp", "flow"],
    }, indent=2))


if __name__ == "__main__":
    main()
