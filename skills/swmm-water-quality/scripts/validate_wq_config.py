#!/usr/bin/env python3
"""Validate a water-quality config JSON against referential and enum constraints.

Stdlib-only; no agentic_swmm imports.

Usage:
    python3 validate_wq_config.py --wq-json path/to/wq.json [--subcatchments-csv path/to/sub.csv]

Exit 0 on success.
Exit 1 on validation failure — writes a JSON error report to stdout.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Enum constants (mirrored from build_swmm_inp.py — no import dependency)
# ---------------------------------------------------------------------------

_WQ_UNITS = {"MG/L", "UG/L", "#/L"}
_BUILDUP_FUNCS = {"POW", "EXP", "SAT"}  # EXT excluded in v1
_WASHOFF_FUNCS = {"EXP", "RC", "EMC"}
_NORMALIZERS = {"AREA", "CURBLENGTH"}


# ---------------------------------------------------------------------------
# Minimal helpers (duplicated from build_swmm_inp.py — cheapest-correct)
# ---------------------------------------------------------------------------


def _require_non_blank(value: Any, *, field: str, context: str) -> str:
    if value is None:
        raise ValueError(f"{context} missing required field '{field}'")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{context} field '{field}' must be a non-blank string")
    return text


def _require_number(
    value: Any,
    *,
    field: str,
    context: str,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if value is None:
        raise ValueError(f"{context} missing required numeric field '{field}'")
    if isinstance(value, bool):
        raise ValueError(f"{context} field '{field}' must be numeric, got boolean")
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
    if min_value is not None and parsed < min_value:
        raise ValueError(f"{context} field '{field}' must be >= {min_value}, got {parsed}")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"{context} field '{field}' must be <= {max_value}, got {parsed}")
    return parsed


# ---------------------------------------------------------------------------
# Core validation logic (canonical implementation)
# ---------------------------------------------------------------------------


def validate_wq_config(wq: dict[str, Any], *, known_subcatchment_ids: set[str] | None = None) -> None:
    """Validate cross-references and enum/range constraints in the WQ JSON.

    Parameters
    ----------
    wq:
        Parsed water-quality config object (the JSON root dict).
    known_subcatchment_ids:
        Optional set of subcatchment IDs for referential checks on
        [COVERAGES] and [LOADINGS].  Pass ``None`` to skip those checks.

    Raises
    ------
    ValueError
        On the first constraint violation found.  Message includes section
        and entry context for easy debugging.
    """
    subs = known_subcatchment_ids or set()

    # ---- [POLLUTANTS] ----
    pollutant_names: set[str] = set()
    for i, p in enumerate(wq.get("pollutants", []), start=1):
        ctx = f"[POLLUTANTS] entry {i}"
        if not isinstance(p, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        name = _require_non_blank(p.get("name"), field="name", context=ctx)
        if " " in name:
            raise ValueError(f"{ctx} 'name' must not contain spaces, got '{name}'")
        if name in pollutant_names:
            raise ValueError(f"[POLLUTANTS] duplicate pollutant name '{name}'")
        pollutant_names.add(name)
        units_raw = _require_non_blank(p.get("units"), field="units", context=ctx)
        if units_raw.upper() not in _WQ_UNITS:
            raise ValueError(f"{ctx} 'units' must be one of {sorted(_WQ_UNITS)}, got '{units_raw}'")
        _require_number(p.get("c_rain", 0.0), field="c_rain", context=ctx, min_value=0.0)
        _require_number(p.get("c_gw", 0.0), field="c_gw", context=ctx, min_value=0.0)
        _require_number(p.get("c_ii", 0.0), field="c_ii", context=ctx, min_value=0.0)
        _require_number(p.get("k_decay_per_day", 0.0), field="k_decay_per_day", context=ctx, min_value=0.0)
        _require_number(p.get("co_fraction", 0.0), field="co_fraction", context=ctx, min_value=0.0, max_value=1.0)
        _require_number(p.get("init_conc", 0.0), field="init_conc", context=ctx, min_value=0.0)

    # ---- [LANDUSES] ----
    landuse_names: set[str] = set()
    for i, lu in enumerate(wq.get("landuses", []), start=1):
        ctx = f"[LANDUSES] entry {i}"
        if not isinstance(lu, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        name = _require_non_blank(lu.get("name"), field="name", context=ctx)
        if name in landuse_names:
            raise ValueError(f"[LANDUSES] duplicate land-use name '{name}'")
        landuse_names.add(name)
        _require_number(lu.get("sweep_interval", 0.0), field="sweep_interval", context=ctx, min_value=0.0)
        _require_number(lu.get("availability", 0.0), field="availability", context=ctx, min_value=0.0, max_value=1.0)
        _require_number(lu.get("last_sweep", 0.0), field="last_sweep", context=ctx, min_value=0.0)

    # ---- [COVERAGES] ----
    coverage_sums: dict[str, float] = {}
    for i, cov in enumerate(wq.get("coverages", []), start=1):
        ctx = f"[COVERAGES] entry {i}"
        if not isinstance(cov, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        sub = _require_non_blank(cov.get("subcatchment"), field="subcatchment", context=ctx)
        lu = _require_non_blank(cov.get("landuse"), field="landuse", context=ctx)
        pct = _require_number(cov.get("percent"), field="percent", context=ctx, min_value=0.0, max_value=100.0)
        if subs and sub not in subs:
            raise ValueError(f"{ctx} 'subcatchment' '{sub}' not found in subcatchments")
        if lu not in landuse_names:
            raise ValueError(f"{ctx} 'landuse' '{lu}' not defined in [LANDUSES]")
        coverage_sums[sub] = coverage_sums.get(sub, 0.0) + pct
    for sub, total in coverage_sums.items():
        if total > 100.0 + 1e-9:
            raise ValueError(
                f"[COVERAGES] subcatchment '{sub}' coverage percents sum to {total:.2f}, must be <= 100"
            )

    # ---- [BUILDUP] ----
    for i, bu in enumerate(wq.get("buildup", []), start=1):
        ctx = f"[BUILDUP] entry {i}"
        if not isinstance(bu, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        lu = _require_non_blank(bu.get("landuse"), field="landuse", context=ctx)
        pol = _require_non_blank(bu.get("pollutant"), field="pollutant", context=ctx)
        ft = _require_non_blank(bu.get("func_type"), field="func_type", context=ctx).upper()
        norm = _require_non_blank(bu.get("normalizer", "AREA"), field="normalizer", context=ctx).upper()
        if lu not in landuse_names:
            raise ValueError(f"{ctx} 'landuse' '{lu}' not defined in [LANDUSES]")
        if pol not in pollutant_names:
            raise ValueError(f"{ctx} 'pollutant' '{pol}' not defined in [POLLUTANTS]")
        if ft == "EXT":
            raise ValueError(
                f"{ctx} FuncType 'EXT' (external time series) is not supported in v1. "
                "Use POW, EXP, or SAT."
            )
        if ft not in _BUILDUP_FUNCS:
            raise ValueError(f"{ctx} 'func_type' must be one of {sorted(_BUILDUP_FUNCS)}, got '{ft}'")
        if norm not in _NORMALIZERS:
            raise ValueError(f"{ctx} 'normalizer' must be AREA or CURBLENGTH, got '{norm}'")
        _require_number(bu.get("c1", 0.0), field="c1", context=ctx)
        _require_number(bu.get("c2", 0.0), field="c2", context=ctx)
        _require_number(bu.get("c3", 0.0), field="c3", context=ctx)

    # ---- [WASHOFF] ----
    for i, wo in enumerate(wq.get("washoff", []), start=1):
        ctx = f"[WASHOFF] entry {i}"
        if not isinstance(wo, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        lu = _require_non_blank(wo.get("landuse"), field="landuse", context=ctx)
        pol = _require_non_blank(wo.get("pollutant"), field="pollutant", context=ctx)
        ft = _require_non_blank(wo.get("func_type"), field="func_type", context=ctx).upper()
        if lu not in landuse_names:
            raise ValueError(f"{ctx} 'landuse' '{lu}' not defined in [LANDUSES]")
        if pol not in pollutant_names:
            raise ValueError(f"{ctx} 'pollutant' '{pol}' not defined in [POLLUTANTS]")
        if ft not in _WASHOFF_FUNCS:
            raise ValueError(f"{ctx} 'func_type' must be one of {sorted(_WASHOFF_FUNCS)}, got '{ft}'")
        _require_number(wo.get("c1", 0.0), field="c1", context=ctx)
        _require_number(wo.get("c2", 0.0), field="c2", context=ctx)
        _require_number(wo.get("sweep_removal", 0.0), field="sweep_removal", context=ctx, min_value=0.0, max_value=1.0)
        _require_number(wo.get("bmp_removal", 0.0), field="bmp_removal", context=ctx, min_value=0.0, max_value=1.0)

    # ---- [LOADINGS] ----
    for i, lo in enumerate(wq.get("loadings", []), start=1):
        ctx = f"[LOADINGS] entry {i}"
        if not isinstance(lo, dict):
            raise ValueError(f"{ctx} must be a JSON object")
        sub = _require_non_blank(lo.get("subcatchment"), field="subcatchment", context=ctx)
        pol = _require_non_blank(lo.get("pollutant"), field="pollutant", context=ctx)
        if subs and sub not in subs:
            raise ValueError(f"{ctx} 'subcatchment' '{sub}' not found in subcatchments")
        if pol not in pollutant_names:
            raise ValueError(f"{ctx} 'pollutant' '{pol}' not defined in [POLLUTANTS]")
        _require_number(lo.get("init_buildup"), field="init_buildup", context=ctx, min_value=0.0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _read_subcatchment_ids(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    ids: set[str] = set()
    for row in rows:
        sid = str(row.get("subcatchment_id", "")).strip()
        if sid:
            ids.add(sid)
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate a water-quality config JSON (referential consistency + enum/range checks)."
    )
    ap.add_argument("--wq-json", type=Path, required=True, help="Path to WQ config JSON")
    ap.add_argument(
        "--subcatchments-csv",
        type=Path,
        default=None,
        help="Optional subcatchments CSV for referential checks on [COVERAGES] and [LOADINGS]",
    )
    args = ap.parse_args()

    try:
        raw = args.wq_json.read_text(encoding="utf-8")
        wq: Any = json.loads(raw)
    except FileNotFoundError:
        report = {"ok": False, "error": f"File not found: {args.wq_json}"}
        print(json.dumps(report, indent=2))
        sys.exit(1)
    except json.JSONDecodeError as exc:
        report = {"ok": False, "error": f"JSON parse error: {exc}"}
        print(json.dumps(report, indent=2))
        sys.exit(1)

    if not isinstance(wq, dict):
        report = {"ok": False, "error": "WQ JSON must be a top-level object"}
        print(json.dumps(report, indent=2))
        sys.exit(1)

    sub_ids: set[str] | None = None
    if args.subcatchments_csv is not None:
        sub_ids = _read_subcatchment_ids(args.subcatchments_csv)

    try:
        validate_wq_config(wq, known_subcatchment_ids=sub_ids)
    except ValueError as exc:
        report = {"ok": False, "error": str(exc)}
        print(json.dumps(report, indent=2))
        sys.exit(1)

    pollutant_count = len(wq.get("pollutants", []))
    landuse_count = len(wq.get("landuses", []))
    report = {
        "ok": True,
        "pollutant_count": pollutant_count,
        "landuse_count": landuse_count,
        "coverage_rows": len(wq.get("coverages", [])),
        "buildup_rows": len(wq.get("buildup", [])),
        "washoff_rows": len(wq.get("washoff", [])),
        "loading_rows": len(wq.get("loadings", [])),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
