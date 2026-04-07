#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

TIME_CANDIDATES = ["timestamp", "time", "datetime", "date_time", "date"]
FLOW_CANDIDATES = ["flow", "discharge", "q", "value"]
DELIMS = [",", "\t", r"\s+"]


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def detect_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:5]
    joined = "\n".join(sample)
    if "," in joined:
        return ","
    if "\t" in joined:
        return "\t"
    return r"\s+"


def read_series(path: str | Path, timestamp_col: str | None = None, flow_col: str | None = None, time_format: str | None = None) -> pd.DataFrame:
    p = Path(path)
    delim = detect_delimiter(p)
    df = pd.read_csv(p, sep=delim, engine="python", comment="#")
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
